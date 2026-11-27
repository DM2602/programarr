import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link, type LinkProps } from "react-router";

export type Variant = "default" | "primary" | "danger" | "ghost";
export type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  default: "border-gray-600 bg-gray-800/80 text-gray-200 hover:bg-gray-700 hover:text-white",
  primary: "border-indigo-500 bg-indigo-600/80 text-white hover:bg-indigo-600",
  danger: "border-red-500 bg-red-600/80 text-white hover:bg-red-600",
  ghost: "border-gray-600 bg-transparent text-gray-200 hover:border-gray-400 hover:text-white",
};

const SIZES: Record<Size, string> = {
  sm: "gap-1.5 px-2.5 py-1.5 text-xs",
  md: "gap-2 px-4 py-2 text-sm",
};

export function buttonClass(variant: Variant = "default", size: Size = "md", extra = ""): string {
  return [
    "inline-flex cursor-pointer items-center justify-center whitespace-nowrap rounded-md border font-medium leading-5",
    "transition duration-150 disabled:cursor-default disabled:opacity-50",
    VARIANTS[variant],
    SIZES[size],
    extra,
  ].join(" ");
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
};

export function Button({ variant = "default", size = "md", icon, className = "", children, type = "button", ...rest }: ButtonProps) {
  return (
    <button type={type} className={buttonClass(variant, size, className)} {...rest}>
      {icon}
      {children}
    </button>
  );
}

type ButtonLinkProps = LinkProps & { variant?: Variant; size?: Size; icon?: ReactNode };

export function ButtonLink({ variant = "default", size = "md", icon, className = "", children, ...rest }: ButtonLinkProps) {
  return (
    <Link className={buttonClass(variant, size, typeof className === "string" ? className : "")} {...rest}>
      {icon}
      {children}
    </Link>
  );
}

type ExternalProps = { href: string; children?: ReactNode; variant?: Variant; size?: Size; icon?: ReactNode; label?: string };

export function ExternalButton({ href, children, variant = "ghost", size = "sm", icon, label }: ExternalProps) {
  return (
    <a className={buttonClass(variant, size)} href={href} target="_blank" rel="noopener noreferrer" title={label} aria-label={label}>
      {icon}
      {children}
    </a>
  );
}
