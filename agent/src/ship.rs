//! Shipping batches of records to the collector, at least once: a batch is retried until the
//! collector accepts it, and only then is the file offset saved. When the collector is busy it
//! answers 429 or 503 with Retry-After; otherwise the agent backs off exponentially.

use std::time::Duration;

use crate::format::Record;

#[derive(serde::Serialize)]
struct Wire<'a> {
    source: &'a str,
    #[serde(flatten)]
    record: &'a Record,
}

pub struct Shipper {
    url: String,
    source: String,
    agent: ureq::Agent,
    pub sent: u64,
    pub retries: u64,
    max_backoff: Duration,
}

impl Shipper {
    pub fn new(collector: &str, source: &str) -> Self {
        let config = ureq::Agent::config_builder()
            .timeout_global(Some(Duration::from_secs(30)))
            .http_status_as_error(false)
            .build();
        Shipper {
            url: format!("{}/ingest", collector.trim_end_matches('/')),
            source: source.to_string(),
            agent: config.into(),
            sent: 0,
            retries: 0,
            max_backoff: Duration::from_secs(30),
        }
    }

    /// Newline-delimited JSON, one record a line.
    pub fn body(&self, batch: &[Record]) -> String {
        let mut out = String::new();
        for r in batch {
            out.push_str(&serde_json::to_string(&Wire { source: &self.source, record: r }).unwrap());
            out.push('\n');
        }
        out
    }

    /// Sends a batch, retrying until the collector takes it.
    pub fn send(&mut self, batch: &[Record]) {
        if batch.is_empty() {
            return;
        }
        let body = self.body(batch);
        let mut backoff = Duration::from_millis(100);
        loop {
            let wait = match self.agent.post(&self.url).header("Content-Type", "application/x-ndjson").send(&body) {
                Ok(resp) if resp.status().is_success() => {
                    self.sent += batch.len() as u64;
                    return;
                }
                Ok(resp) => {
                    let asked = resp
                        .headers()
                        .get("Retry-After")
                        .and_then(|v| v.to_str().ok())
                        .and_then(|v| v.parse::<u64>().ok())
                        .map(Duration::from_secs);
                    eprintln!("collector answered {}; retrying", resp.status());
                    asked.unwrap_or(backoff)
                }
                Err(e) => {
                    eprintln!("collector unreachable ({e}); retrying");
                    backoff
                }
            };
            self.retries += 1;
            std::thread::sleep(wait);
            backoff = (backoff * 2).min(self.max_backoff);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Read, Write};
    use std::net::TcpListener;

    /// A collector that refuses the first request with 429 and then accepts, recording bodies.
    fn fake_collector() -> (String, std::thread::JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = format!("http://{}", listener.local_addr().unwrap());
        let handle = std::thread::spawn(move || {
            let mut bodies = Vec::new();
            for (i, stream) in listener.incoming().take(2).enumerate() {
                let mut stream = stream.unwrap();
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut len = 0;
                loop {
                    let mut line = String::new();
                    reader.read_line(&mut line).unwrap();
                    if line == "\r\n" {
                        break;
                    }
                    if let Some(v) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                        len = v.trim().parse().unwrap();
                    }
                }
                let mut body = vec![0; len];
                reader.read_exact(&mut body).unwrap();
                bodies.push(String::from_utf8(body).unwrap());
                let reply = if i == 0 {
                    "HTTP/1.1 429 Too Many Requests\r\nRetry-After: 0\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                } else {
                    "HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n"
                };
                stream.write_all(reply.as_bytes()).unwrap();
            }
            bodies
        });
        (addr, handle)
    }

    #[test]
    fn retries_until_accepted() {
        let (url, server) = fake_collector();
        let mut s = Shipper::new(&url, "test");
        let r = Record {
            timestamp: 1.5,
            level: "INFO".into(),
            component: "c".into(),
            session: "s".into(),
            message: "m \"quoted\"".into(),
            label: String::new(),
        };
        s.send(&[r.clone(), r]);
        let bodies = server.join().unwrap();
        assert_eq!(s.sent, 2);
        assert_eq!(s.retries, 1);
        assert_eq!(bodies[0], bodies[1]); // the same batch, sent again
        let first: serde_json::Value = serde_json::from_str(bodies[0].lines().next().unwrap()).unwrap();
        assert_eq!(first["source"], "test");
        assert_eq!(first["message"], "m \"quoted\"");
    }
}
