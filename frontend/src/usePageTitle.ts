import { useEffect } from 'react';

export function usePageTitle(title: string | undefined) {
  useEffect(() => {
    // A nested view supplies its own title when the parent passes undefined.
    if (title !== undefined) document.title = title;
  }, [title]);
}
