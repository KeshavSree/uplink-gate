import type { ReactNode } from "react";

/** A console panel: a thin title bar and a body. */
export function Panel({
  title,
  aside,
  className = "",
  children,
}: {
  title: string;
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`panel ${className}`} aria-label={title}>
      <header className="panel-bar">
        <h2>{title}</h2>
        {aside ? <div className="panel-aside">{aside}</div> : null}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}
