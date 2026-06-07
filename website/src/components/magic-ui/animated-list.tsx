"use client";

import { cn } from "@/lib/utils";
import { AnimatePresence, motion } from "framer-motion";
import React, {
  ComponentPropsWithoutRef,
  ReactElement,
  useEffect,
  useMemo,
  useState,
} from "react";

export function AnimatedList({
  className,
  children,
  delay = 1000,
}: {
  className?: string;
  children: React.ReactNode;
  delay?: number;
}) {
  const [index, setIndex] = useState(0);
  const childrenArray = useMemo(
    () => React.Children.toArray(children) as ReactElement[],
    [children],
  );

  useEffect(() => {
    if (index < childrenArray.length - 1) {
      const timeout = setTimeout(() => setIndex((prev) => prev + 1), delay);
      return () => clearTimeout(timeout);
    }
  }, [index, delay, childrenArray.length]);

  const itemsToShow = useMemo(
    () => childrenArray.slice(0, index + 1).reverse(),
    [index, childrenArray],
  );

  return (
    <div className={cn("flex flex-col items-center gap-4", className)}>
      <AnimatePresence>
        {itemsToShow.map((item) => (
          <AnimatedListItem key={(item as ReactElement).key}>
            {item}
          </AnimatedListItem>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function AnimatedListItem({
  children,
}: ComponentPropsWithoutRef<"div">) {
  const animations = {
    initial: { scale: 0, opacity: 0 },
    animate: { scale: 1, opacity: 1, originY: 0 },
    exit: { scale: 0, opacity: 0 },
    transition: { type: "spring" as const, stiffness: 350, damping: 40 },
  };

  return (
    <motion.div className="mx-auto w-full" {...animations}>
      {children}
    </motion.div>
  );
}
