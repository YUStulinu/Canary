//! Reading a log file line by line from a byte offset, and following it as it grows (like
//! `tail -F`): a file that shrinks has been truncated or replaced, and is read again from its start.

use std::fs::File;
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::time::Duration;

pub struct Tail {
    path: PathBuf,
    reader: BufReader<File>,
    /// Offset just past the last complete line returned.
    pub offset: u64,
    partial: String,
    follow: bool,
    poll: Duration,
}

impl Tail {
    pub fn open(path: &Path, offset: u64, follow: bool) -> std::io::Result<Self> {
        let mut file = File::open(path)?;
        let len = file.metadata()?.len();
        let offset = if offset > len { 0 } else { offset };
        file.seek(SeekFrom::Start(offset))?;
        Ok(Tail {
            path: path.to_path_buf(),
            reader: BufReader::with_capacity(1 << 20, file),
            offset,
            partial: String::new(),
            follow,
            poll: Duration::from_millis(200),
        })
    }

    /// The next complete line (without its newline), or None at the end of a file not followed.
    /// A line still being written (no newline yet) is held back until it is complete.
    pub fn next_line(&mut self) -> std::io::Result<Option<String>> {
        loop {
            let mut buf = Vec::new();
            let n = self.reader.read_until(b'\n', &mut buf)?;
            if n > 0 {
                self.partial.push_str(&String::from_utf8_lossy(&buf));
                if self.partial.ends_with('\n') {
                    self.offset += self.partial.len() as u64;
                    let mut line = std::mem::take(&mut self.partial);
                    line.truncate(line.trim_end_matches(['\n', '\r']).len());
                    return Ok(Some(line));
                }
                continue;
            }
            if !self.follow {
                return Ok(None);
            }
            std::thread::sleep(self.poll);
            // Truncated or replaced by a shorter file: start over from the beginning.
            let len = std::fs::metadata(&self.path).map(|m| m.len()).unwrap_or(0);
            let held = self.offset + self.partial.len() as u64;
            if len < held {
                let file = File::open(&self.path)?;
                self.reader = BufReader::with_capacity(1 << 20, file);
                self.offset = 0;
                self.partial.clear();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn reads_from_an_offset_and_holds_back_partial_lines() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("a.log");
        std::fs::write(&path, "one\ntwo\nthr").unwrap();
        let mut t = Tail::open(&path, 4, false).unwrap();
        assert_eq!(t.next_line().unwrap().as_deref(), Some("two"));
        assert_eq!(t.offset, 8);
        assert_eq!(t.next_line().unwrap(), None); // "thr" has no newline yet
    }

    #[test]
    fn follows_growth_and_truncation() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("b.log");
        std::fs::write(&path, "a\n").unwrap();
        let p = path.clone();
        let writer = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(300));
            let mut f = std::fs::OpenOptions::new().append(true).open(&p).unwrap();
            f.write_all(b"b\n").unwrap();
            drop(f);
            std::thread::sleep(Duration::from_millis(500));
            std::fs::write(&p, "c\n").unwrap(); // rotated: a new, shorter file
        });
        let mut t = Tail::open(&path, 0, true).unwrap();
        t.poll = Duration::from_millis(50);
        let got: Vec<String> = (0..3).map(|_| t.next_line().unwrap().unwrap()).collect();
        writer.join().unwrap();
        assert_eq!(got, ["a", "b", "c"]);
    }
}
