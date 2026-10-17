// Draws the CodeLens mark (the dashboard's icon, apps/web/src/app/icon.svg: a
// ring, a triangle, three dots, on a 32-unit grid) into the PNGs the app
// needs: the installer's and window's icon, and the tray's at 1x and 2x. The
// shapes are simple enough to rasterize directly, 4x4 samples a pixel for
// smooth edges, with no image library. The outputs are committed:
//
//   node scripts/render-icons.mjs
//
// One violet that reads on light and dark taskbars alike (the dashboard's
// --chart-1 in dark mode, #8b78ee).

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "assets");
const COLOR = [0x8b, 0x78, 0xee];
const SAMPLES = 4;

const TRIANGLE = [
  [11, 19],
  [16, 11],
  [21, 19],
];
const DOTS = TRIANGLE;

function distanceToSegment(px, py, [ax, ay], [bx, by]) {
  const dx = bx - ax;
  const dy = by - ay;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Is the point (in the 32-unit grid) inked? */
function inked(x, y) {
  if (Math.abs(Math.hypot(x - 16, y - 16) - 13) <= 1.5) return true; // the ring, stroke 3
  for (let i = 0; i < 3; i++) {
    if (distanceToSegment(x, y, TRIANGLE[i], TRIANGLE[(i + 1) % 3]) <= 0.8) return true; // stroke 1.6
  }
  return DOTS.some(([cx, cy]) => Math.hypot(x - cx, y - cy) <= 2.6);
}

function render(size) {
  const rgba = Buffer.alloc(size * size * 4);
  const unit = 32 / size;
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      let hits = 0;
      for (let sy = 0; sy < SAMPLES; sy++) {
        for (let sx = 0; sx < SAMPLES; sx++) {
          const x = (px + (sx + 0.5) / SAMPLES) * unit;
          const y = (py + (sy + 0.5) / SAMPLES) * unit;
          if (inked(x, y)) hits++;
        }
      }
      const i = (py * size + px) * 4;
      rgba.set(COLOR, i);
      rgba[i + 3] = Math.round((255 * hits) / (SAMPLES * SAMPLES));
    }
  }
  return png(size, rgba);
}

// --- PNG: signature, IHDR, one IDAT (each row prefixed by filter 0), IEND.

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(buffer) {
  let c = 0xffffffff;
  for (const byte of buffer) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}

function png(size, rgba) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header.set([8, 6, 0, 0, 0], 8); // 8 bits, RGBA, deflate, no filter method, no interlace
  const rows = [];
  for (let y = 0; y < size; y++) {
    rows.push(Buffer.from([0]), rgba.subarray(y * size * 4, (y + 1) * size * 4));
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.concat(rows))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

mkdirSync(OUT, { recursive: true });
for (const [name, size] of [
  ["icon.png", 256],
  ["tray.png", 16],
  ["tray@2x.png", 32],
]) {
  writeFileSync(join(OUT, name), render(size));
  console.log(`${name}: ${size}x${size}`);
}
