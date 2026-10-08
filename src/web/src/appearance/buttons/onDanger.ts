/** Light danger fills need dark text. Night palettes use #f0aaa0, which crosses this line. */
export function onDangerColor(danger: string): string {
  const match = /^#([0-9a-f]{6})$/i.exec(danger.trim());
  if (!match) return "#ffffff";
  const value = Number.parseInt(match[1], 16);
  const red = (value >> 16) & 255;
  const green = (value >> 8) & 255;
  const blue = value & 255;
  const luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255;
  return luminance > 0.62 ? "#2c1416" : "#ffffff";
}
