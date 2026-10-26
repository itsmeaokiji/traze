import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';

export function BrowseTable({ label, children }: { label: string; children: ReactNode }) {
  const container = useRef<HTMLDivElement>(null);
  const hintId = useId();
  const [scrollable, setScrollable] = useState(false);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const measure = () => setScrollable(element.scrollWidth > element.clientWidth + 1);
    measure();
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(measure);
      observer.observe(element);
      const table = element.querySelector('table');
      if (table) observer.observe(table);
      return () => observer.disconnect();
    }
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [children]);

  return <>
    {scrollable && <p id={hintId} className="field-help browse-scroll-hint">Scroll horizontally to view all columns.</p>}
    <div ref={container} className="table-scroll browse-records" tabIndex={scrollable ? 0 : undefined}
      role={scrollable ? 'region' : undefined} aria-label={scrollable ? `${label} scroll area` : undefined}
      aria-describedby={scrollable ? hintId : undefined}>{children}</div>
  </>;
}
