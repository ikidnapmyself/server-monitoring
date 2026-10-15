# Vendored frontend files

The only third-party frontend files in this repository. There is no npm and no
build step. `apps/console/_tests/test_vendor.py` fails if a file here changes
or a file appears that this table does not list.

| File | Version | Source | SHA-256 |
|---|---|---|---|
| `htmx.min.js` | 2.0.11 | https://unpkg.com/htmx.org@2.0.11/dist/htmx.min.js | `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717` |
| `octicons.svg` | 19.15.1 | https://unpkg.com/@primer/octicons@19.15.1/build/svg/ | `3b1adecba43a3465ddebad832ccb88a2dd58d51a596c4743a38c267243b73c0e` |

`octicons.svg` is a sprite assembled from that version's individual `<name>-16.svg` files.
Each icon is one `<symbol id="octicon-<name>" viewBox="0 0 16 16">` holding the upstream
`<path>` elements unchanged. It carries no script, style or event attribute. Octicons are
MIT licensed. To add an icon, fetch its `-16.svg` from the source above at the pinned
version, add its paths as a new symbol, and update the hash.

## Upgrading htmx

1. Read the upstream changelog for every version between the pinned one and the new one.
2. Fetch the new file from two CDNs and confirm the hashes agree. All three hashes below
   must be identical:

   ```bash
   curl -sSfL "https://unpkg.com/htmx.org@<version>/dist/htmx.min.js" -o /tmp/htmx.min.js
   shasum -a 256 /tmp/htmx.min.js
   curl -sSfL "https://cdn.jsdelivr.net/npm/htmx.org@<version>/dist/htmx.min.js" | shasum -a 256
   ```

3. Replace the file, update the row above, run `uv run pytest apps/console`.
