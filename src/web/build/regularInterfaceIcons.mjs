/** Icon.tsx fixes the public family to regular weight. Prune unused vendor
 * weights at build time, preserving the vendor's SVG and IconBase behavior.
 * No node_modules file is changed. An incompatible package fails explicitly.
 */
export function regularInterfaceIcons() {
  return {
    name: 'regular-interface-icons',
    transform(code, id) {
      if (!id.replaceAll('\\', '/').includes('/@phosphor-icons/react/dist/defs/')) return null;
      const ast = this.parse(code);
      const map = ast.body.flatMap(node => node.type === 'VariableDeclaration' ? node.declarations : [])
        .find(node => node.init?.type === 'NewExpression' && node.init.callee.name === 'Map')?.init.arguments[0];
      const regular = map?.elements?.find(node => node.type === 'ArrayExpression' && node.elements[0]?.value === 'regular');
      if (!regular) throw new Error(`Unsupported Phosphor weight definition: ${id}`);
      return { code: code.slice(0, map.start) + '[' + code.slice(regular.start, regular.end) + ']' + code.slice(map.end), map: null };
    },
  };
}
