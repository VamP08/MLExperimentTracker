# parity_reference

This is the original Express implementation of the API. **It is not part of the product,
it is not needed to install or run MLExperimentTracker, and nothing in the Python package
imports or starts it.** If you are here to use the tracker, go back to the repository root
and read that README instead.

It is kept for one reason: it is what the port is measured against.

## Why it is still in the tree

The dashboard in `ml_frontend/` was written against this server, so its payloads are the
requirement. A key renamed, a number turned into a string, an object that gained a level
of nesting — each of those breaks a page rather than a test. When the API was rewritten in
Python, "the rewrite preserved behaviour" was a claim, and the only honest way to hold a
claim like that is a diff against the thing it replaced.

So `tests/test_parity.py` starts this server as a subprocess on a free port, with
`EXPERIMENT_STORAGE_PATH` pointed at the same fixture tree the Python server is reading,
and issues the same request to both. It compares the responses field by field — keys,
nesting, types, values, and key order — over 16 URLs covering every read route, both
query-parameter variants that change the response shape, and two deliberate misses so the
404 bodies are compared too, plus the CSV export compared as text. The result is zero
structural divergence.

Delete this directory and that evidence goes with it, replaced by an assertion in prose.

## Where the port deliberately differs

The port is not a transcription. It diverges in five places, every one of them a fix, and
every one asserted *as* a divergence by a test in `tests/test_parity.py` so it has to be
re-decided to be undone:

1. **`undefined` becomes `null`.** `JSON.stringify` drops a key whose value is
   `undefined`, so this server returns 20 keys for a bare run and 25 for a complete one. A
   fixed key set is easier to type against, and both are falsy in React.
2. **The CSV is RFC 4180 quoted.** This server joins raw values with commas, so one value
   containing a comma shifts every column to its right — in a file people open in a
   spreadsheet.
3. **Malformed input degrades instead of 500ing the page.** Here a non-array `tags`, or a
   run with no `created_at`, takes the whole dashboard down for every project.
4. **A non-finite metric costs its own value, not its entire row.**
5. **A stored experiment description is read back.** This server writes
   `project_metadata.json` on `PATCH /api/experiment/{id}` and opens it nowhere, so the
   edit reverts on the next load.

Those defects are still live in this code, on purpose. It is the reference for what the
format used to mean, not a second copy of the product to be maintained.

## Running it by hand

You do not need to, but if you want to see it answer for itself:

```bash
cd parity_reference
npm install
EXPERIMENT_STORAGE_PATH=/path/to/your/storage PORT=5050 node index.js
```

Then `curl http://127.0.0.1:5050/api/dashboard`. With no `EXPERIMENT_STORAGE_PATH` it
falls back to `~/.experiment_tracker`, the same default the Python package uses, which is
what lets the two be pointed at one tree and compared.

`node_modules/` is gitignored; the parity suite skips its live half when Node or
`parity_reference/node_modules` is missing, so `npm install` here is what turns those
tests on.

## Dependencies

`express`, `cors`, `dotenv`, and `nodemon` for the dev script. Everything else that was
once declared — `mongoose`, `mongodb`, `bcrypt`, `jsonwebtoken`, `multer`, `typescript`,
`ts-node` — was removed along with the five Mongoose models that were the only importers
of any of it. None of it was ever reachable from a route: this server reads the storage
tree off the filesystem and has never connected to a database.
