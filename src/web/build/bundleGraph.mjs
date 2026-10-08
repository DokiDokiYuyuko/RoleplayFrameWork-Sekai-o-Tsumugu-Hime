import { posix } from 'node:path';
import { parseAst } from 'rollup/parseAst';

/** Count the entry and every transitive static import once. Lazy imports do not
 * belong to startup, but remain covered by the independent total/route budgets.
 */
export async function staticBundleClosure(entry, sources) {
  const seen = new Set();
  function visit(name) {
    if (seen.has(name)) return;
    const source = sources.get(name);
    if (source == null) throw new Error(`Missing static bundle dependency: ${name}`);
    seen.add(name);
    for (const item of parseAst(source).body) {
      if (!['ImportDeclaration', 'ExportNamedDeclaration', 'ExportAllDeclaration'].includes(item.type)) continue;
      const target = item.source?.value;
      if (!target?.startsWith('.')) continue;
      const dependency = posix.normalize(posix.join(posix.dirname(name), target));
      visit(dependency);
    }
  }
  visit(entry);
  return [...seen];
}
