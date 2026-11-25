import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";

const styles: Record<ButtonVariant, string> = {
  primary:
    "rounded-md bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700 active:scale-[0.99] disabled:opacity-50",
  secondary:
    "rounded-md border border-line bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 active:scale-[0.99] disabled:opacity-50",
  quiet:
    "rounded-md px-2 py-1 text-sm font-medium text-slate-600 underline-offset-2 transition hover:text-brand-700 hover:underline disabled:opacity-50",
  danger:
    "rounded-md border border-rose-200 bg-white px-3 py-1.5 text-sm font-medium text-rose-700 transition hover:bg-rose-50 active:scale-[0.99] disabled:opacity-50",
};

export function buttonClass(variant: ButtonVariant = "secondary", extra = ""): string {
  return `${styles[variant]} ${extra}`.trim();
}

export function Button({
  variant = "secondary",
  className = "",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return <button type={type} className={buttonClass(variant, className)} {...props} />;
}
