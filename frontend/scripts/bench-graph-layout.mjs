/**
 * Measures the page-graph layout on synthetic workspaces, in node, so the numbers
 * quoted in the CHANGELOG can be reproduced:
 *
 *     npm run bench:layout             # 25 / 111 / 400 / 800 pages
 *     npm run bench:layout 2000        # a specific size
 *
 * Times are the layout alone. Browser time is higher (React commit + SVG paint), and
 * dev-mode React invokes the layout twice, so measure end-to-end changes in a
 * production build with a browser, not with this script.
 *
 * The quality columns guard the approximation: if Barnes-Hut is ever retuned into
 * producing a worse graph than the exact O(n²) pass it replaced, edge length, node
 * overlap and frame occupancy will move. The exact pass is in git history, in the
 * commit that introduced `src/graph/layout.ts`.
 */
import { build } from "esbuild";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const sizes = process.argv.slice(2).length ? process.argv.slice(2).map(Number) : [25, 111, 400, 800];
const out = join(mkdtempSync(join(tmpdir(), "agentkb-layout-")), "layout.mjs");
await build({ entryPoints: ["src/graph/layout.ts"], bundle: true, format: "esm", outfile: out, logLevel: "error" });
const { layoutGraph } = await import(pathToFileURL(out).href);

/** Same shape as the browser harness: a few hubs, the rest hanging off them. */
function syntheticWorkspace(count, seed = 7) {
  let state = seed;
  const random = () => ((state = (state * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
  const nodes = Array.from({ length: count }, (_, i) => ({ id: `wiki/pages/p${i}.md`, label: `页面 ${i}` }));
  const hubs = Math.max(1, Math.round(count * 0.08));
  const edges = [];
  for (let i = hubs; i < count; i += 1) {
    edges.push({ source: nodes[i].id, target: nodes[Math.floor(random() * hubs)].id });
    if (random() < 0.35) edges.push({ source: nodes[i].id, target: nodes[Math.floor(random() * count)].id });
  }
  for (let i = 0; i < hubs - 1; i += 1) edges.push({ source: nodes[i].id, target: nodes[i + 1].id });
  return { nodes, edges };
}

function quality(nodes, edges, placed) {
  const index = new Map(nodes.map((node, i) => [node.id, i]));
  const lengths = edges
    .map((edge) => {
      const a = placed[index.get(edge.source)];
      const b = placed[index.get(edge.target)];
      return Math.hypot(a.x - b.x, a.y - b.y);
    })
    .sort((a, b) => a - b);
  let overlapping = 0;
  for (let i = 0; i < placed.length; i += 1) {
    for (let j = i + 1; j < placed.length; j += 1) {
      const gap =
        Math.hypot(placed[i].x - placed[j].x, placed[i].y - placed[j].y) - placed[i].radius - placed[j].radius;
      if (gap < 0) overlapping += 1;
    }
  }
  const xs = placed.map((node) => node.x);
  const ys = placed.map((node) => node.y);
  return {
    median: lengths[Math.floor(lengths.length / 2)],
    p90: lengths[Math.floor(lengths.length * 0.9)],
    overlapping,
    spanX: Math.round(Math.max(...xs) - Math.min(...xs)),
    spanY: Math.round(Math.max(...ys) - Math.min(...ys)),
  };
}

console.log("页   边    布局耗时   边长中位  p90    重叠对  占用");
for (const count of sizes) {
  const { nodes, edges } = syntheticWorkspace(count);
  layoutGraph(nodes, edges, 960, 620);
  const runs = count >= 400 ? 3 : 5;
  const times = [];
  let placed = [];
  for (let i = 0; i < runs; i += 1) {
    const started = performance.now();
    placed = layoutGraph(nodes, edges, 960, 620);
    times.push(performance.now() - started);
  }
  const median = times.sort((a, b) => a - b)[Math.floor(runs / 2)];
  const q = quality(nodes, edges, placed);
  console.log(
    `${String(count).padStart(4)} ${String(edges.length).padStart(5)} ${median.toFixed(0).padStart(8)}ms ` +
      `${q.median.toFixed(1).padStart(9)} ${q.p90.toFixed(1).padStart(6)} ${String(q.overlapping).padStart(7)} ` +
      `${q.spanX}×${q.spanY}`,
  );
}
