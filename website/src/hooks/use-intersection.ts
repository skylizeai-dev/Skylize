"use client";

import { RefObject, useEffect, useRef, useState } from "react";

interface UseIntersectionOptions {
  threshold?: number | number[];
  rootMargin?: string;
  root?: Element | null;
  once?: boolean;
}

export function useIntersection<T extends Element>(
  options: UseIntersectionOptions = {},
): [RefObject<T | null>, boolean] {
  const { once = true, threshold, rootMargin, root } = options;
  const ref = useRef<T | null>(null);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          if (once) observer.disconnect();
        } else if (!once) {
          setIsVisible(false);
        }
      },
      { threshold, rootMargin, root },
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [once, threshold, rootMargin, root]);

  return [ref, isVisible];
}
