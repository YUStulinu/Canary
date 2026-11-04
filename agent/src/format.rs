//! Splitting log lines into header fields and the message, for the formats Canary knows.
//! The detector's Python parser (detector/canary/logformat.py) does the same; both are tested
//! against tests/fixtures/lines.json.

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    Hdfs,
    Bgl,
}

impl std::str::FromStr for Format {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, String> {
        match s.to_ascii_lowercase().as_str() {
            "hdfs" => Ok(Format::Hdfs),
            "bgl" => Ok(Format::Bgl),
            _ => Err(format!("unknown format {s} (hdfs or bgl)")),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Record {
    /// Seconds since the epoch, UTC.
    pub timestamp: f64,
    pub level: String,
    pub component: String,
    /// HDFS: the block the line is about; BGL: the node.
    pub session: String,
    pub message: String,
    /// BGL's own alert label ("-" for normal lines); empty for other formats.
    pub label: String,
}

pub fn parse(format: Format, line: &str) -> Option<Record> {
    match format {
        Format::Hdfs => parse_hdfs(line),
        Format::Bgl => parse_bgl(line),
    }
}

/// "081109 203518 143 INFO dfs.DataNode$DataXceiver: Receiving block blk_-1608999687919862906 ..."
fn parse_hdfs(line: &str) -> Option<Record> {
    let parts: Vec<&str> = line.trim_end_matches(['\r', '\n']).splitn(6, ' ').collect();
    if parts.len() < 6 || parts[0].len() != 6 || parts[1].len() != 6 {
        return None;
    }
    let num = |s: &str| s.parse::<i64>().ok();
    let (d, t) = (parts[0], parts[1]);
    let ts = epoch(
        2000 + num(&d[0..2])?,
        num(&d[2..4])?,
        num(&d[4..6])?,
        num(&t[0..2])?,
        num(&t[2..4])?,
        num(&t[4..6])?,
    );
    let message = parts[5].to_string();
    Some(Record {
        timestamp: ts as f64,
        level: parts[3].to_string(),
        component: parts[4].trim_end_matches(':').to_string(),
        session: block_id(&message).unwrap_or_default(),
        message,
        label: String::new(),
    })
}

/// The first block id ("blk_-1608999687919862906") in a message.
pub fn block_id(message: &str) -> Option<String> {
    let start = message.find("blk_")?;
    let rest = &message[start + 4..];
    let len = rest
        .char_indices()
        .take_while(|&(i, c)| c.is_ascii_digit() || (i == 0 && c == '-'))
        .count();
    let digits = &rest[..len];
    if digits.trim_start_matches('-').is_empty() {
        return None;
    }
    Some(format!("blk_{digits}"))
}

/// "- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.675872 R02-M1-N0-C:J12-U11 RAS KERNEL INFO message"
fn parse_bgl(line: &str) -> Option<Record> {
    let parts: Vec<&str> = line.trim_end_matches(['\r', '\n']).splitn(10, ' ').collect();
    if parts.len() < 9 {
        return None;
    }
    let seconds: i64 = parts[1].parse().ok()?;
    let fraction = parts[4]
        .rsplit_once('.')
        .and_then(|(_, f)| format!("0.{f}").parse::<f64>().ok())
        .unwrap_or(0.0);
    Some(Record {
        timestamp: seconds as f64 + fraction,
        level: parts[8].to_string(),
        component: parts[7].to_string(),
        session: parts[3].to_string(),
        message: parts.get(9).copied().unwrap_or("").to_string(),
        label: parts[0].to_string(),
    })
}

/// Seconds since 1970-01-01 for a UTC date and time (days-from-civil, proleptic Gregorian).
fn epoch(y: i64, m: i64, d: i64, hh: i64, mm: i64, ss: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe - 719468;
    days * 86400 + hh * 3600 + mm * 60 + ss
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(serde::Deserialize)]
    struct Case {
        format: String,
        line: String,
        parsed: Option<Expected>,
    }
    #[derive(serde::Deserialize)]
    struct Expected {
        timestamp: f64,
        level: String,
        component: String,
        session: String,
        message: String,
        label: String,
    }

    #[test]
    fn shared_fixture() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../tests/fixtures/lines.json");
        let cases: Vec<Case> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        assert!(cases.len() >= 8);
        for c in cases {
            let got = parse(c.format.parse().unwrap(), &c.line);
            match (got, c.parsed) {
                (None, None) => {}
                (Some(g), Some(e)) => {
                    assert!((g.timestamp - e.timestamp).abs() < 1e-5, "{}: {} vs {}", c.line, g.timestamp, e.timestamp);
                    assert_eq!(
                        (g.level, g.component, g.session, g.message, g.label),
                        (e.level, e.component, e.session, e.message, e.label),
                        "{}",
                        c.line
                    );
                }
                (g, e) => panic!("{}: parsed {:?}, expected {}", c.line, g, e.is_some()),
            }
        }
    }

    #[test]
    fn epoch_matches_known_dates() {
        assert_eq!(epoch(1970, 1, 1, 0, 0, 0), 0);
        assert_eq!(epoch(2008, 11, 9, 20, 35, 18), 1226262918);
        assert_eq!(epoch(2000, 2, 29, 12, 0, 0), 951825600);
    }

    #[test]
    fn block_ids() {
        assert_eq!(block_id("to blk_-42 and blk_7").as_deref(), Some("blk_-42"));
        assert_eq!(block_id("blk_ nothing"), None);
    }
}
