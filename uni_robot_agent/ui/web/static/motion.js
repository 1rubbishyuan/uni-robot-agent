const active = new WeakMap();
export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
export function animate(node, frames, duration = 220) {
  active.get(node)?.cancel();
  if (reducedMotion()) return null;
  const animation = node.animate(frames, {
    duration,
    easing: "cubic-bezier(.2,.8,.2,1)",
  });
  active.set(node, animation);
  return animation;
}
export function reveal(node, direction = 1) {
  animate(node, [
    { opacity: 0.35, transform: `translateX(${direction * 8}px)` },
    { opacity: 1, transform: "translateX(0)" },
  ]);
}
