// Package clickhouse writes rows to ClickHouse through its HTTP interface.
package clickhouse

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// Row is one log line as stored (see db/schema.sql).
type Row struct {
	Seq       uint64  `json:"seq"`
	Timestamp float64 `json:"ts"`
	Source    string  `json:"source"`
	Level     string  `json:"level"`
	Component string  `json:"component"`
	Session   string  `json:"session"`
	Message   string  `json:"message"`
	Label     string  `json:"label"`
}

type Client struct {
	URL, User, Password, Database string
	HTTP                          *http.Client
}

func New(base, user, password, database string) *Client {
	return &Client{URL: strings.TrimRight(base, "/"), User: user, Password: password, Database: database,
		HTTP: &http.Client{Timeout: 60 * time.Second}}
}

func (c *Client) do(query string, body io.Reader) ([]byte, error) {
	u := c.URL + "/?database=" + url.QueryEscape(c.Database) + "&query=" + url.QueryEscape(query) +
		// Timestamps arrive as seconds with a fraction.
		"&date_time_input_format=best_effort"
	req, err := http.NewRequest(http.MethodPost, u, body)
	if err != nil {
		return nil, err
	}
	req.SetBasicAuth(c.User, c.Password)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	out, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("clickhouse: %s: %s", resp.Status, bytes.TrimSpace(out))
	}
	return out, nil
}

// Insert writes rows to the logs table in one request.
func (c *Client) Insert(rows []Row) error {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	for i := range rows {
		if err := enc.Encode(&rows[i]); err != nil {
			return err
		}
	}
	_, err := c.do("INSERT INTO logs (seq, ts, source, level, component, session, message, label) FORMAT JSONEachRow", &buf)
	return err
}

// MaxSeq is the highest sequence number already stored (0 for an empty table).
func (c *Client) MaxSeq() (uint64, error) {
	out, err := c.do("SELECT max(seq) FROM logs", nil)
	if err != nil {
		return 0, err
	}
	return strconv.ParseUint(strings.TrimSpace(string(out)), 10, 64)
}
