//! canary-agent: follows a log file, parses its lines and ships them to Canary's collector.
//!
//!   canary-agent --file /var/log/hdfs.log --format hdfs --source hdfs-prod --collector http://127.0.0.1:8070 --follow
//!   canary-agent --file data/raw/HDFS_v1/HDFS.log --format hdfs --rate 50000 --limit 2000000   (replaying a dataset)
//!
//! The byte offset of the last line the collector accepted is kept in a state file, so a restarted
//! agent carries on where it stopped and no line is lost (some may be sent twice).

mod format;
mod ship;
mod tail;

use std::path::PathBuf;
use std::time::{Duration, Instant};

use clap::Parser;

use format::Format;

#[derive(Parser)]
#[command(about = "Ships log lines to the Canary collector")]
struct Args {
    #[arg(long)]
    file: PathBuf,
    #[arg(long)]
    format: Format,
    #[arg(long, default_value = "http://127.0.0.1:8070")]
    collector: String,
    /// Name of this log stream
    #[arg(long)]
    source: Option<String>,
    /// Keep reading as the file grows
    #[arg(long)]
    follow: bool,
    /// Lines per batch
    #[arg(long, default_value_t = 2000)]
    batch: usize,
    /// Longest wait before a partial batch is sent, milliseconds
    #[arg(long, default_value_t = 500)]
    flush_ms: u64,
    /// At most this many lines a second (0: as fast as possible), for replaying a dataset
    #[arg(long, default_value_t = 0)]
    rate: u64,
    /// Stop after this many lines (0: no limit)
    #[arg(long, default_value_t = 0)]
    limit: u64,
    /// Where the offset is kept (default: next to the log, "<file>.canary-offset")
    #[arg(long)]
    state: Option<PathBuf>,
}

fn main() -> std::io::Result<()> {
    let args = Args::parse();
    let state = args.state.clone().unwrap_or_else(|| {
        let mut p = args.file.clone().into_os_string();
        p.push(".canary-offset");
        PathBuf::from(p)
    });
    let offset = std::fs::read_to_string(&state).ok().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
    let source = args.source.clone().unwrap_or_else(|| format!("{:?}", args.format).to_lowercase());
    let mut tail = tail::Tail::open(&args.file, offset, args.follow)?;
    let mut shipper = ship::Shipper::new(&args.collector, &source);
    let started = Instant::now();
    let mut batch = Vec::with_capacity(args.batch);
    let mut last_flush = Instant::now();
    let (mut read, mut unparsed) = (0u64, 0u64);
    eprintln!("shipping {} from byte {offset} to {}", args.file.display(), args.collector);

    let flush = |batch: &mut Vec<format::Record>, shipper: &mut ship::Shipper, offset: u64| -> std::io::Result<()> {
        shipper.send(batch);
        batch.clear();
        std::fs::write(&state, offset.to_string())
    };

    while let Some(line) = tail.next_line()? {
        read += 1;
        match format::parse(args.format, &line) {
            Some(r) => batch.push(r),
            None => unparsed += 1,
        }
        if batch.len() >= args.batch || last_flush.elapsed() >= Duration::from_millis(args.flush_ms) {
            flush(&mut batch, &mut shipper, tail.offset)?;
            last_flush = Instant::now();
        }
        if args.rate > 0 {
            // Keep to the requested rate on average.
            let due = Duration::from_secs_f64(read as f64 / args.rate as f64);
            if let Some(wait) = due.checked_sub(started.elapsed()) {
                std::thread::sleep(wait);
            }
        }
        if read % 1_000_000 == 0 {
            eprintln!("{read} lines, {:.0} lines/s", read as f64 / started.elapsed().as_secs_f64());
        }
        if args.limit > 0 && read >= args.limit {
            break;
        }
    }
    flush(&mut batch, &mut shipper, tail.offset)?;
    let secs = started.elapsed().as_secs_f64();
    println!(
        "{{\"lines\": {read}, \"unparsed\": {unparsed}, \"shipped\": {}, \"retries\": {}, \"seconds\": {secs:.1}, \"lines_per_second\": {:.0}}}",
        shipper.sent,
        shipper.retries,
        read as f64 / secs
    );
    Ok(())
}
