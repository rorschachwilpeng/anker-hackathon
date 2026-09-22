# Live smoke evidence (Task 1 implementation, before review)

- /after-sales/extract: HTTP 200, 2.81s; result {"order_id":"DEMO20260910001","request_type":"exchange","expected_solution":"换大一码"}.
- /chat first attempt: HTTP 200 SSE but only upstream_error event, no done; not a successful chat. Failed occurrence raw upstream payload was not captured, so attribution unresolved.
- /chat explicit retry: HTTP 200, 5.20s; 19 nonempty token events then done(stop). Reconstructed response acknowledges DEMO20260910001 and asks exchange vs return.
- /chat second turn: actual first user + actual assistant response supplied in history; HTTP 200, 1.82s; 6 content events reconstruct DEMO20260910001, then done(stop).
- No hidden automatic retries added. Result proves paths work in this run, not provider reliability guarantees.
- Commands use curl -N against localhost, no API key passed to curl.
