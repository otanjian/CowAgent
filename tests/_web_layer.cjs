// Source of the console's whole web layer, for structural assertions.
//
// The fork's handler bodies used to sit in channel/web/web_channel.py; the
// web-split change (openspec/changes/adopt-upstream-web-split) moved the fork's
// implementation into channel/web/fork/ and left the entry module with the URL
// table and the handler imports.
//
// A test that greps one file after that move passes or fails for the wrong
// reason: the entry module no longer contains the handler bodies it is looking
// for, so a server-side contract can look satisfied while the code that
// implements it is missing entirely. Read the layer instead. This mirrors
// tests/_helpers.web_layer_source() on the Python side.
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'channel', 'web');

function pythonFiles(dir) {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            return entry.name === '__pycache__' ? [] : pythonFiles(full);
        }
        return entry.name.endsWith('.py') ? [full] : [];
    });
}

const entry = path.join(WEB, 'web_channel.py');
const PARTS = [entry, ...pythonFiles(WEB).filter(p => p !== entry).sort()];

function readWebLayer() {
    return PARTS.map(p => fs.readFileSync(p, 'utf8')).join('\n\n');
}

module.exports = { readWebLayer };
