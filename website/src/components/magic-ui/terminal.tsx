"use client";

import { cn } from "@/lib/utils";
import { motion, MotionProps } from "framer-motion";
import { ReactNode } from "react";

interface TerminalProps {
  children: ReactNode;
  className?: string;
}

interface AnimatedSpanProps extends MotionProps {
  children: ReactNode;
  className?: string;
  delay?: number;
}

interface TypingAnimationProps {
  children: string;
  className?: string;
  duration?: number;
  delay?: number;
}

export function Terminal({ children, className }: TerminalProps) {
  return (
    <div
      className={cn(
        "z-0 h-full max-h-[400px] w-full max-w-lg rounded-xl border border-border bg-background",
        className,
      )}
    >
      <div className="flex flex-col gap-y-2 border-b border-border p-4">
        <div className="flex flex-row gap-x-2">
          <div className="h-2 w-2 rounded-full bg-red-500"></div>
          <div className="h-2 w-2 rounded-full bg-yellow-500"></div>
          <div className="h-2 w-2 rounded-full bg-green-500"></div>
        </div>
      </div>
      <pre className="p-4">
        <code className="grid gap-y-1 overflow-auto text-sm">{children}</code>
      </pre>
    </div>
  );
}

export function AnimatedSpan({
  children,
  className,
  delay = 0,
  ...props
}: AnimatedSpanProps) {
  return (
    <motion.span
      className={cn("flex items-center gap-1 text-muted-foreground", className)}
      initial={{ opacity: 0, y: -5 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay }}
      {...props}
    >
      {children}
    </motion.span>
  );
}

export function TypingAnimation({
  children,
  className,
  duration = 200,
  delay = 0,
}: TypingAnimationProps) {
  const characters = children.split("");

  return (
    <motion.span className={cn("text-foreground", className)}>
      {characters.map((char, i) => (
        <motion.span
          key={`${i}-${char}`}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{
            delay: delay + (i * duration) / 1000,
            duration: 0.1,
          }}
        >
          {char}
        </motion.span>
      ))}
    </motion.span>
  );
}
