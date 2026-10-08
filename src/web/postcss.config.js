import tailwindcss from 'tailwindcss';
import autoprefixer from 'autoprefixer';
import { legacyLayer } from './build/legacyLayer.mjs';

// Order matters: Tailwind expands first, then the legacy sheets move into
// `@layer legacy`, then prefixes are added inside and outside the layer.
export default {
  plugins: [tailwindcss(), legacyLayer(), autoprefixer()],
};
