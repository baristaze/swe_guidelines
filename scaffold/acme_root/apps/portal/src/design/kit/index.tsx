// A minimal component kit over the tokens and kit.css. Components render;
// nothing here decides. The one state a component keeps is its own: whether
// a menu is open, and where the keyboard is in it.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { tokens } from "../tokens";

export * from "./icons";

/** The product's mark: a check in the accent square, and the name. */
export function BrandMark({ size = 28, withName = true }: { size?: number; withName?: boolean }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: tokens.space.sm }}>
      <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true">
        <rect width="28" height="28" rx="8" fill={tokens.color.accent} />
        <path
          d="M8.5 14.5l3.8 3.8 7.2-8.1"
          fill="none"
          stroke={tokens.color.accentText}
          strokeWidth="2.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {withName ? <span style={{ fontWeight: 650, fontSize: tokens.font.size.lg, letterSpacing: "-0.01em" }}>Acme</span> : null}
    </span>
  );
}

/** A page: one centered column under the chrome's bar. `narrow` is the
 * sign-in and sign-up width, a single column of fields under the mark; the
 * side padding shrinks with a narrow window. A page has a title, or a heading
 * of its own in its place. */
export function Page({
  title,
  heading,
  back,
  nav,
  notice,
  narrow = false,
  children,
}: {
  title?: string;
  /** Drawn where the title goes, for a page whose heading is more than words. */
  heading?: ReactNode;
  /** The way back, above the title. */
  back?: ReactNode;
  nav?: ReactNode;
  /** What the page says above its title before anything else: a banner the
   * whole app shows. */
  notice?: ReactNode;
  narrow?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="acme-app">
      {nav ? (
        <header className="acme-topbar">
          <div className="acme-topbar-inner">{nav}</div>
        </header>
      ) : null}
      <main className="acme-page" data-narrow={narrow || undefined}>
        <div className="acme-column">
          {narrow ? (
            <div className="acme-brand">
              <BrandMark size={36} withName={false} />
            </div>
          ) : null}
          {notice ? <div className="acme-page-notice">{notice}</div> : null}
          {back ? <div className="acme-back">{back}</div> : null}
          {heading ?? <h1 className="acme-title">{title}</h1>}
          {/* minmax(0, 1fr): a row that never wraps shrinks with the column instead of widening it. */}
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: tokens.space.lg }}>{children}</div>
        </div>
      </main>
    </div>
  );
}

/** `id` is where a link on the page lands. */
export function Card({ title, id, children }: { title?: string; id?: string; children: ReactNode }) {
  return (
    <section className="acme-card" id={id}>
      {title ? <h2 className="acme-card-title">{title}</h2> : null}
      {children}
    </section>
  );
}

export function Button({
  children,
  onClick,
  type = "button",
  disabled,
  tone = "accent",
  wide = false,
}: {
  children: ReactNode;
  onClick?: () => void;
  type?: "button" | "submit";
  disabled?: boolean;
  tone?: "accent" | "danger" | "plain";
  wide?: boolean;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="acme-button"
      data-tone={tone}
      data-wide={wide || undefined}
    >
      {children}
    </button>
  );
}

export function TextField({
  label,
  value,
  onChange,
  type = "text",
  autoComplete,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "email" | "password" | "date";
  autoComplete?: string;
  placeholder?: string;
}) {
  return (
    <label className="acme-label">
      <span>{label}</span>
      <input
        type={type}
        value={value}
        autoComplete={autoComplete}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="acme-field"
      />
    </label>
  );
}

export function Banner({ children }: { children: ReactNode }) {
  return (
    <div role="status" className="acme-banner">
      <svg className="acme-banner-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="6.75" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path d="M8 4.75v3.75" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="8" cy="11" r="0.9" fill="currentColor" />
      </svg>
      <div className="acme-banner-body">{children}</div>
    </div>
  );
}

/** What went wrong, in the danger colour, announced to a screen reader. */
export function ErrorText({ children }: { children: ReactNode }) {
  return (
    <span role="alert" className="acme-error">
      {children}
    </span>
  );
}

export function Muted({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <span style={{ color: tokens.color.muted, ...style }}>{children}</span>;
}

export function Table({ headers, rows }: { headers: string[]; rows: ReactNode[][] }) {
  return (
    <table className="acme-table">
      <thead>
        <tr>
          {headers.map((header) => (
            <th key={header}>{header}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((cells, index) => (
          <tr key={index}>
            {cells.map((cell, cellIndex) => (
              <td key={cellIndex}>{cell}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Pill({
  children,
  title,
  tone = "plain",
}: {
  children: ReactNode;
  title?: string;
  tone?: "plain" | "accent" | "danger";
}) {
  return (
    <span title={title} className="acme-pill" data-tone={tone}>
      {children}
    </span>
  );
}

export function LinkButton({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="acme-link">
      {children}
    </button>
  );
}

/** `large` is the control that stands as a page's heading. */
export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
  large = false,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
  large?: boolean;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="acme-segmented" data-large={large || undefined}>
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option.value)}
            className="acme-segment"
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function TextArea({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="acme-label">
      <span>{label}</span>
      <textarea value={value} rows={3} onChange={(event) => onChange(event.target.value)} className="acme-field" />
    </label>
  );
}

export function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="acme-label">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} className="acme-field">
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** A square button that shows an icon; the label is what a screen reader says. */
export function IconButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} className="acme-icon-button">
      {children}
    </button>
  );
}

interface MenuState {
  /** Closes the menu; the keyboard goes back to the button that opened it. */
  close: () => void;
}

const MenuContext = createContext<MenuState>({ close: () => undefined });

/** The items the arrows move through, in order; a disabled one is skipped. */
function menuItems(menu: HTMLElement | null): HTMLElement[] {
  if (!menu) return [];
  return [...menu.querySelectorAll<HTMLElement>('[role="menuitem"], [role="menuitemradio"]')].filter(
    (item) => !(item as HTMLButtonElement).disabled,
  );
}

/** A button that opens a menu under it. A click, Enter, or Space opens it and
 * the keyboard lands on the first item (ArrowUp on the button lands on the
 * last); the arrows, Home, and End move; Escape closes it and puts the
 * keyboard back on the button; a click anywhere else or Tab closes it. */
export function Menu({
  label,
  trigger,
  triggerLabel,
  triggerTitle,
  triggerClassName = "acme-menu-trigger",
  align = "start",
  minWidth = 220,
  disabled = false,
  children,
}: {
  /** What a screen reader calls the open menu. */
  label: string;
  /** What the button shows. */
  trigger: ReactNode;
  /** What a screen reader calls the button, when its content does not say it. */
  triggerLabel?: string;
  triggerTitle?: string;
  triggerClassName?: string;
  /** Which edge of the button the menu lines up with. */
  align?: "start" | "end";
  minWidth?: number;
  disabled?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const landOn = useRef<"first" | "last">("first");
  const id = useId();

  const close = useCallback(() => {
    setOpen(false);
    button.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const items = menuItems(menu.current);
    (landOn.current === "last" ? items[items.length - 1] : items[0])?.focus();
    const outside = (event: Event) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  const onButtonKey = (event: KeyboardEvent) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    landOn.current = event.key === "ArrowUp" ? "last" : "first";
    setOpen(true);
  };

  const onMenuKey = (event: KeyboardEvent) => {
    const items = menuItems(menu.current);
    const at = items.indexOf(document.activeElement as HTMLElement);
    const go = (index: number) => items[(index + items.length) % items.length]?.focus();
    if (event.key === "ArrowDown") go(at + 1);
    else if (event.key === "ArrowUp") go(at < 0 ? -1 : at - 1);
    else if (event.key === "Home") go(0);
    else if (event.key === "End") go(-1);
    else if (event.key === "Escape") close();
    else if (event.key === "Tab") setOpen(false);
    else return;
    if (event.key !== "Tab") event.preventDefault();
  };

  return (
    <div ref={root} style={{ position: "relative", minWidth: 0, display: "flex" }}>
      <button
        ref={button}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={triggerLabel}
        title={triggerTitle}
        disabled={disabled}
        onClick={() => {
          landOn.current = "first";
          setOpen((value) => !value);
        }}
        onKeyDown={onButtonKey}
        className={triggerClassName}
      >
        {trigger}
      </button>
      {open ? (
        <div
          ref={menu}
          id={id}
          role="menu"
          aria-label={label}
          onKeyDown={onMenuKey}
          className="acme-menu"
          style={{ position: "absolute", top: "calc(100% + 6px)", [align === "end" ? "right" : "left"]: 0, zIndex: 30, minWidth }}
        >
          <MenuContext.Provider value={{ close }}>{children}</MenuContext.Provider>
        </div>
      ) : null}
    </div>
  );
}

/** One thing a menu does. It closes the menu, then does it. */
export function MenuItem({
  onSelect,
  disabled,
  tone = "plain",
  icon,
  children,
}: {
  onSelect: () => void;
  disabled?: boolean;
  /** `danger` is a choice that changes many things at once, in red. */
  tone?: "plain" | "danger";
  /** A 16-pixel mark before the words, from the kit's icons. */
  icon?: ReactNode;
  children: ReactNode;
}) {
  const { close } = useContext(MenuContext);
  return (
    <button
      type="button"
      role="menuitem"
      tabIndex={-1}
      disabled={disabled}
      data-tone={tone === "danger" ? tone : undefined}
      onClick={() => {
        close();
        onSelect();
      }}
      className="acme-menu-item"
    >
      <MenuIcon icon={icon} />
      <span className="acme-menu-label">{children}</span>
    </button>
  );
}

/** One of a set of choices, the current one checked. The menu stays open, so
 * the choice is seen taking effect. */
export function MenuItemRadio({
  checked,
  onSelect,
  icon,
  children,
}: {
  checked: boolean;
  onSelect: () => void;
  /** A 16-pixel mark before the words; the check stays at the far end. */
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={checked}
      tabIndex={-1}
      onClick={onSelect}
      className="acme-menu-item"
    >
      <MenuIcon icon={icon} />
      <span className="acme-menu-label">{children}</span>
      {checked ? (
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" style={{ color: tokens.color.link }}>
          <path d="M3 7.5l2.75 2.75L11 4.5" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ) : null}
    </button>
  );
}

/** The slot an item's icon sits in: a fixed width, so the words line up
 * down the menu. An item with no icon in a menu where others have one
 * keeps the same empty space (kit.css). */
function MenuIcon({ icon }: { icon: ReactNode }) {
  if (icon === undefined || icon === null) return null;
  return (
    <span className="acme-menu-icon" aria-hidden="true">
      {icon}
    </span>
  );
}

/** Words in a menu that are not a choice: a group's name, or a fact. */
export function MenuText({ children, strong = false }: { children: ReactNode; strong?: boolean }) {
  return (
    <div role="presentation" className="acme-menu-text" data-strong={strong || undefined}>
      {children}
    </div>
  );
}

export function MenuSeparator() {
  return <div role="separator" className="acme-menu-separator" />;
}

/** The small down-pointing mark on a button that opens a menu. */
export function Caret() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" style={{ flexShrink: 0 }}>
      <path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Three dots: the mark of a button that opens more actions. */
export function MoreIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" style={{ flexShrink: 0 }}>
      <circle cx="3.5" cy="8" r="1.4" fill="currentColor" />
      <circle cx="8" cy="8" r="1.4" fill="currentColor" />
      <circle cx="12.5" cy="8" r="1.4" fill="currentColor" />
    </svg>
  );
}

/** A cross: the mark of a button that closes or clears. */
export function CloseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" style={{ flexShrink: 0 }}>
      <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

/** A card whose body folds away under its title. The title is a button that
 * says whether the body is shown (`aria-expanded`), so a click, Enter, or
 * Space folds and unfolds it; `actions` sit at the other end of the title
 * row and stay reachable while it is folded. A folded body stays mounted
 * and hidden, so what it holds is not read again when it unfolds. */
export function FoldingCard({
  title,
  folded,
  onToggle,
  actions,
  children,
}: {
  title: string;
  folded: boolean;
  onToggle: () => void;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const bodyId = useId();
  return (
    <section className="acme-card" data-folded={folded || undefined}>
      <div className="acme-fold-head">
        <h2 className="acme-card-title acme-fold-title">
          <button type="button" className="acme-fold-toggle" aria-expanded={!folded} aria-controls={bodyId} onClick={onToggle}>
            <span className="acme-fold-caret">
              <Caret />
            </span>
            {title}
          </button>
        </h2>
        {actions ? <div className="acme-fold-actions">{actions}</div> : null}
      </div>
      <div id={bodyId} hidden={folded} className="acme-fold-body">
        {children}
      </div>
    </section>
  );
}

/** The focusable things inside an element, in order: what Tab moves through. */
function focusables(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return [
    ...root.querySelectorAll<HTMLElement>("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"),
  ].filter((element) => !(element as HTMLButtonElement).disabled);
}

/** A question over the page before a change of many things: the title asks
 * it, the body says what follows, and two buttons answer. The keyboard lands
 * on `Cancel`, Tab stays inside, Escape and a click on the backdrop cancel,
 * and closing puts the keyboard back where it was. */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  tone = "accent",
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  tone?: "accent" | "danger";
  /** The confirm waits: its answer, or a number it needs, is not in yet. */
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  useReturnFocus();
  useEffect(() => cancel.current?.focus(), []);
  const onKey = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusables(dialog.current);
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  };
  return (
    <div className="acme-dialog-backdrop" onClick={onCancel}>
      <div
        ref={dialog}
        className="acme-dialog acme-confirm"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKey}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id={titleId} className="acme-card-title">
          {title}
        </h2>
        {children ? <div className="acme-confirm-body">{children}</div> : null}
        <div className="acme-confirm-actions">
          <button ref={cancel} type="button" className="acme-button" data-tone="plain" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="acme-button" data-tone={tone} disabled={busy} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Puts the keyboard back on what held it when the component mounted, once
 * the component goes. */
function useReturnFocus(): void {
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    return () => before?.focus?.();
  }, []);
}

/** A bar at the foot of the window for what is selected: what is selected,
 * the actions that apply to it, and a way to let it go. A toolbar, so a
 * screen reader names it and Tab reaches it after the page. */
export function ActionBar({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      {/* Room at the foot of the page, so the bar never covers its last row. */}
      <div className="acme-action-bar-room" aria-hidden="true" />
      <div role="toolbar" aria-label={label} className="acme-action-bar">
        {children}
      </div>
    </>
  );
}

/** A short word that a change was made, with at most one action (Undo) and
 * a way to dismiss it. Neutral, not a warning: nothing went wrong. */
export function Toast({
  children,
  action,
  onAction,
  onDismiss,
}: {
  children: ReactNode;
  action?: string;
  onAction?: () => void;
  onDismiss: () => void;
}) {
  return (
    <div role="status" className="acme-toast">
      <span className="acme-toast-body">{children}</span>
      {action && onAction ? (
        <button type="button" className="acme-toast-action" onClick={onAction}>
          {action}
        </button>
      ) : null}
      <button type="button" className="acme-toast-close" aria-label="Dismiss" title="Dismiss" onClick={onDismiss}>
        <CloseIcon />
      </button>
    </div>
  );
}
