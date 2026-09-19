/**
 * Force-directed page layout, hand-rolled and deterministic.
 *
 * A wiki is normally tens of pages, so the simplest O(n²) repulsion pass was fine
 * and the graph stayed free of a WebGL layout dependency. A workspace with several
 * hundred pages made that assumption visible — opening the graph took about a second
 * in a production build — so the repulsion pass now uses a Barnes-Hut approximation
 * while keeping the same seeded start, the same forces and the same clamped frame.
 */

export type LayoutNode = { id: string };
export type LayoutEdge = { source: string; target: string };
export type PlacedNode = { x: number; y: number; radius: number; degree: number };

/** Deterministic PRNG so the graph keeps the same shape between renders. */
function seededRandom(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const STEPS = 240;
const COOLING = 0.985;
const PADDING = 62;

export function layoutGraph<N extends LayoutNode>(
  nodes: N[],
  edges: LayoutEdge[],
  width: number,
  height: number,
): Array<N & PlacedNode> {
  const count = nodes.length;
  if (!count) return [];

  const index = new Map(nodes.map((node, position) => [node.id, position]));
  const links = edges
    .map((edge) => [index.get(edge.source), index.get(edge.target)] as const)
    .filter((pair): pair is readonly [number, number] => pair[0] !== undefined && pair[1] !== undefined);
  const degrees = new Array<number>(count).fill(0);
  for (const [a, b] of links) {
    degrees[a] += 1;
    degrees[b] += 1;
  }

  const random = seededRandom(20260822);
  const xs = new Float64Array(count);
  const ys = new Float64Array(count);
  const dx = new Float64Array(count);
  const dy = new Float64Array(count);
  for (let i = 0; i < count; i += 1) {
    const angle = (i / count) * Math.PI * 2 + random() * 0.6;
    const spread = Math.min(width, height) * (0.2 + random() * 0.2);
    xs[i] = width / 2 + Math.cos(angle) * spread;
    ys[i] = height / 2 + Math.sin(angle) * spread;
  }

  const ideal = Math.sqrt((width * height) / count) * 0.72;
  let temperature = Math.min(width, height) / 6;
  const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));
  const tree = new Quadtree();

  for (let step = 0; step < STEPS; step += 1) {
    dx.fill(0);
    dy.fill(0);
    tree.repulsion(xs, ys, dx, dy, ideal, count);
    for (const [a, b] of links) {
      const vx = xs[a] - xs[b];
      const vy = ys[a] - ys[b];
      const distance = Math.max(0.01, Math.sqrt(vx * vx + vy * vy));
      const force = (distance * distance) / ideal;
      const ux = (vx / distance) * force;
      const uy = (vy / distance) * force;
      dx[a] -= ux;
      dy[a] -= uy;
      dx[b] += ux;
      dy[b] += uy;
    }
    for (let i = 0; i < count; i += 1) {
      dx[i] += (width / 2 - xs[i]) * 0.015;
      dy[i] += (height / 2 - ys[i]) * 0.015;
    }
    temperature *= COOLING;
    for (let i = 0; i < count; i += 1) {
      const distance = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) || 1;
      const limited = Math.min(distance, temperature);
      // Keep the layout inside the frame; otherwise the graph drifts far past the
      // viewBox and the final fit shrinks every node into a blob.
      xs[i] = clamp(xs[i] + (dx[i] / distance) * limited, PADDING, width - PADDING);
      ys[i] = clamp(ys[i] + (dy[i] / distance) * limited, PADDING, height - PADDING);
    }
  }

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < count; i += 1) {
    if (xs[i] < minX) minX = xs[i];
    if (xs[i] > maxX) maxX = xs[i];
    if (ys[i] < minY) minY = ys[i];
    if (ys[i] > maxY) maxY = ys[i];
  }
  const spanX = Math.max(1, maxX - minX);
  const spanY = Math.max(1, maxY - minY);
  // Already inside the frame, so this only centres the result and nudges a
  // sparse graph up slightly towards the available area.
  const scale = Math.min(1.2, (width - PADDING * 2) / spanX, (height - PADDING * 2) / spanY);
  const offsetX = (width - spanX * scale) / 2 - minX * scale;
  const offsetY = (height - spanY * scale) / 2 - minY * scale;

  return nodes.map((node, i) => ({
    ...node,
    x: xs[i] * scale + offsetX,
    y: ys[i] * scale + offsetY,
    radius: Math.min(15, 4.4 + Math.sqrt(degrees[i]) * 2.6),
    degree: degrees[i],
  }));
}

/**
 * Barnes-Hut quadtree over the current positions.
 *
 * Repulsion is the expensive part of the layout: every step compares every pair, so a
 * workspace with hundreds of pages spent most of its opening time here. Distant pages are
 * now approximated by the centre of mass of the cell that contains them. `THETA` is the
 * accuracy knob — a cell counts as one body when its width divided by the distance is below
 * it — and leaves are always exact, so nearby pages keep attracting/repelling individually.
 */
const THETA = 1;
/** Points per leaf. Small enough to stay exact locally, large enough to keep the tree flat. */
const BUCKET = 4;
/** Below this cell size subdivision stops, so coincident pages cannot recurse forever. */
const MIN_HALF = 0.5;
/** Depth bound, so the per-level scratch buffer has a fixed size. */
const MAX_DEPTH = 16;

class Quadtree {
  private capacity = 0;
  private used = 0;
  private child = new Int32Array(4);
  private half = new Float64Array(1);
  private centerX = new Float64Array(1);
  private centerY = new Float64Array(1);
  private bucket = new Int32Array(BUCKET);
  private bucketCount = new Int32Array(1);
  private mass = new Float64Array(1);
  private massX = new Float64Array(1);
  private massY = new Float64Array(1);
  private stack = new Int32Array(64);
  /** Held points during a subdivision, one slice per recursion level, so redistributing
   *  a leaf never allocates (a 800-page graph subdivides tens of thousands of times). */
  private scratch = new Int32Array(BUCKET * MAX_DEPTH);

  repulsion(
    xs: Float64Array,
    ys: Float64Array,
    dx: Float64Array,
    dy: Float64Array,
    ideal: number,
    count: number,
  ): void {
    if (count < 2) return;
    this.build(xs, ys, count);
    this.aggregate(xs, ys);
    this.apply(xs, ys, dx, dy, ideal * ideal, count);
  }

  private build(xs: Float64Array, ys: Float64Array, count: number): void {
    this.reset(count);
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (let i = 0; i < count; i += 1) {
      if (xs[i] < minX) minX = xs[i];
      if (xs[i] > maxX) maxX = xs[i];
      if (ys[i] < minY) minY = ys[i];
      if (ys[i] > maxY) maxY = ys[i];
    }
    const half = Math.max(1, maxX - minX, maxY - minY) * 0.5 + 1;
    this.half[0] = half;
    this.centerX[0] = (minX + maxX) * 0.5;
    this.centerY[0] = (minY + maxY) * 0.5;
    for (let i = 0; i < count; i += 1) this.insert(i, xs, ys);
  }

  private reset(count: number): void {
    // One leaf cell can hold BUCKET points, and every subdivision creates four children,
    // so a few nodes per point is a safe starting size; grow() handles the rest.
    const capacity = Math.max(64, count * 2);
    if (this.capacity < capacity) {
      this.capacity = capacity;
      this.child = new Int32Array(capacity * 4);
      this.half = new Float64Array(capacity);
      this.centerX = new Float64Array(capacity);
      this.centerY = new Float64Array(capacity);
      this.bucket = new Int32Array(capacity * BUCKET);
      this.bucketCount = new Int32Array(capacity);
      this.mass = new Float64Array(capacity);
      this.massX = new Float64Array(capacity);
      this.massY = new Float64Array(capacity);
      this.stack = new Int32Array(capacity);
    }
    this.used = 1;
    this.child.fill(-1, 0, 4);
    this.bucketCount.fill(0, 0, this.capacity);
  }

  private grow(): void {
    const capacity = this.capacity * 2;
    const copy = <T extends Int32Array | Float64Array>(source: T, size: number): T => {
      const next = source.slice(0, size) as T;
      return next;
    };
    const child = new Int32Array(capacity * 4);
    child.set(this.child.subarray(0, this.used * 4));
    this.child = child;
    this.half = copy(this.half, capacity);
    this.centerX = copy(this.centerX, capacity);
    this.centerY = copy(this.centerY, capacity);
    this.bucketCount = copy(this.bucketCount, capacity);
    this.mass = copy(this.mass, capacity);
    this.massX = copy(this.massX, capacity);
    this.massY = copy(this.massY, capacity);
    this.stack = copy(this.stack, capacity);
    const bucket = new Int32Array(capacity * BUCKET);
    bucket.set(this.bucket.subarray(0, this.used * BUCKET));
    this.bucket = bucket;
    this.capacity = capacity;
  }

  private insert(point: number, xs: Float64Array, ys: Float64Array): void {
    const x = xs[point];
    const y = ys[point];
    let node = 0;
    for (;;) {
      if (this.child[node * 4] < 0) {
        const base = node * BUCKET;
        if (this.bucketCount[node] < BUCKET) {
          this.bucket[base + this.bucketCount[node]] = point;
          this.bucketCount[node] += 1;
          return;
        }
        if (this.half[node] <= MIN_HALF) {
          // Coincident points, or a frame too small to separate them: the parent's
          // aggregate still attracts this one, an exact position is meaningless here.
          return;
        }
        this.subdivide(node, 0, xs, ys);
        continue;
      }
      node = this.childIndex(node, x, y);
    }
  }

  private subdivide(node: number, depth: number, xs: Float64Array, ys: Float64Array): void {
    const scratch = depth * BUCKET;
    const base = node * BUCKET;
    for (let k = 0; k < BUCKET; k += 1) this.scratch[scratch + k] = this.bucket[base + k];
    this.bucketCount[node] = 0;
    this.open(node);
    // The held points are re-inserted below this cell; each may push a child cell past
    // its bucket size in turn, which recurses one level deeper.
    for (let k = 0; k < BUCKET; k += 1) {
      const point = this.scratch[scratch + k];
      this.insertInto(this.childIndex(node, xs[point], ys[point]), point, depth + 1, xs, ys);
    }
  }

  /** Insert below `node`, whose geometry is already set; used when redistributing a leaf. */
  private insertInto(node: number, point: number, depth: number, xs: Float64Array, ys: Float64Array): void {
    const x = xs[point];
    const y = ys[point];
    let current = node;
    let level = depth;
    for (;;) {
      if (this.child[current * 4] < 0) {
        const base = current * BUCKET;
        if (this.bucketCount[current] < BUCKET) {
          this.bucket[base + this.bucketCount[current]] = point;
          this.bucketCount[current] += 1;
          return;
        }
        if (this.half[current] <= MIN_HALF || level >= MAX_DEPTH) return;
        const scratch = level * BUCKET;
        for (let k = 0; k < BUCKET; k += 1) this.scratch[scratch + k] = this.bucket[base + k];
        this.bucketCount[current] = 0;
        this.open(current);
        level += 1;
        for (let k = 0; k < BUCKET; k += 1) {
          const held = this.scratch[scratch + k];
          this.insertInto(this.childIndex(current, xs[held], ys[held]), held, level, xs, ys);
        }
        continue;
      }
      current = this.childIndex(current, x, y);
    }
  }

  /** Turn a leaf into an internal node with four empty children. */
  private open(node: number): void {
    if (this.used + 4 > this.capacity) this.grow();
    const half = this.half[node] * 0.5;
    const childX = this.centerX[node];
    const childY = this.centerY[node];
    for (let quadrant = 0; quadrant < 4; quadrant += 1) {
      const index = this.used + quadrant;
      this.child[index * 4] = -1;
      this.child[index * 4 + 1] = -1;
      this.child[index * 4 + 2] = -1;
      this.child[index * 4 + 3] = -1;
      this.half[index] = half;
      this.centerX[index] = childX + (quadrant & 1 ? half : -half);
      this.centerY[index] = childY + (quadrant & 2 ? half : -half);
      this.bucketCount[index] = 0;
    }
    this.child[node * 4] = this.used;
    this.child[node * 4 + 1] = this.used + 1;
    this.child[node * 4 + 2] = this.used + 2;
    this.child[node * 4 + 3] = this.used + 3;
    this.used += 4;
  }

  private childIndex(node: number, x: number, y: number): number {
    const quadrant = (x >= this.centerX[node] ? 1 : 0) + (y >= this.centerY[node] ? 2 : 0);
    return this.child[node * 4 + quadrant];
  }

  /** Centre of mass per cell, bottom-up: children are always allocated after their parent. */
  private aggregate(xs: Float64Array, ys: Float64Array): void {
    for (let node = this.used - 1; node >= 0; node -= 1) {
      if (this.child[node * 4] < 0) {
        const base = node * BUCKET;
        const size = this.bucketCount[node];
        let sumX = 0;
        let sumY = 0;
        for (let k = 0; k < size; k += 1) {
          sumX += xs[this.bucket[base + k]];
          sumY += ys[this.bucket[base + k]];
        }
        this.mass[node] = size;
        this.massX[node] = size ? sumX / size : 0;
        this.massY[node] = size ? sumY / size : 0;
        continue;
      }
      let mass = 0;
      let sumX = 0;
      let sumY = 0;
      for (let quadrant = 0; quadrant < 4; quadrant += 1) {
        const child = this.child[node * 4 + quadrant];
        const childMass = this.mass[child];
        mass += childMass;
        sumX += this.massX[child] * childMass;
        sumY += this.massY[child] * childMass;
      }
      this.mass[node] = mass;
      this.massX[node] = mass ? sumX / mass : 0;
      this.massY[node] = mass ? sumY / mass : 0;
    }
  }

  private apply(
    xs: Float64Array,
    ys: Float64Array,
    dx: Float64Array,
    dy: Float64Array,
    squaredIdeal: number,
    count: number,
  ): void {
    const stack = this.stack;
    for (let i = 0; i < count; i += 1) {
      const xi = xs[i];
      const yi = ys[i];
      let top = 0;
      stack[top += 1] = 0;
      while (top > 0) {
        const node = stack[top];
        top -= 1;
        const mass = this.mass[node];
        if (mass === 0) continue;
        if (this.child[node * 4] < 0) {
          const base = node * BUCKET;
          const size = this.bucketCount[node];
          for (let k = 0; k < size; k += 1) {
            const j = this.bucket[base + k];
            if (j === i) continue;
            let vx = xi - xs[j];
            let vy = yi - ys[j];
            let distance = Math.hypot(vx, vy);
            if (distance < 0.01) {
              // Two pages clamped to the same corner: push them apart deterministically.
              const angle = j * 2.399963;
              vx = Math.cos(angle) * 0.01;
              vy = Math.sin(angle) * 0.01;
              distance = 0.01;
            }
            const ux = (vx / distance) * (squaredIdeal / distance);
            const uy = (vy / distance) * (squaredIdeal / distance);
            dx[i] += ux;
            dy[i] += uy;
          }
          continue;
        }
        const vx = xi - this.massX[node];
        const vy = yi - this.massY[node];
        const distance = Math.sqrt(vx * vx + vy * vy);
        // Open the cell when it is too close to treat as one body, and always when the
        // visiting point may be inside it, so the aggregate never repels itself.
        if (distance < 1e-3 || (this.half[node] * 2) / distance > THETA) {
          const base = node * 4;
          for (let quadrant = 0; quadrant < 4; quadrant += 1) {
            stack[top += 1] = this.child[base + quadrant];
          }
          continue;
        }
        const force = (mass * squaredIdeal) / distance;
        dx[i] += (vx / distance) * force;
        dy[i] += (vy / distance) * force;
      }
    }
  }
}
