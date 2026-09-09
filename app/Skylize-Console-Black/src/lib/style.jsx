import React, { useState } from 'react';

/** Parses a CSS declaration string ("color:red;font-size:12px") into a React style object. */
export function sx(str) {
  if (!str) return undefined;
  const out = {};
  String(str)
    .split(';')
    .forEach((decl) => {
      const idx = decl.indexOf(':');
      if (idx === -1) return;
      const prop = decl.slice(0, idx).trim();
      const val = decl.slice(idx + 1).trim();
      if (!prop || !val) return;
      const camel = prop.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      out[camel] = val;
    });
  return out;
}

/**
 * Renders `as` with `style` applied, merging in `hoverStyle` on hover and/or
 * `focusStyle` on focus — a functional stand-in for the source design's
 * `style-hover` / `style-focus` attributes.
 */
export function Interactive({ as: Tag = 'div', style, hoverStyle, focusStyle, children, ...rest }) {
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const merged = { ...style, ...(hover && hoverStyle), ...(focus && focusStyle) };
  return (
    <Tag
      {...rest}
      style={merged}
      onMouseEnter={(e) => { setHover(true); rest.onMouseEnter && rest.onMouseEnter(e); }}
      onMouseLeave={(e) => { setHover(false); rest.onMouseLeave && rest.onMouseLeave(e); }}
      onFocus={(e) => { setFocus(true); rest.onFocus && rest.onFocus(e); }}
      onBlur={(e) => { setFocus(false); rest.onBlur && rest.onBlur(e); }}
    >
      {children}
    </Tag>
  );
}
