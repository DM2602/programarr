import { Button } from "./Button";

export function Pager({ page, pages, onPage, newer = "Previous", older = "Next" }: {
  page: number;
  pages: number;
  onPage: (page: number) => void;
  newer?: string;
  older?: string;
}) {
  if (pages <= 1) return null;
  return (
    <nav className="mt-10 flex items-center justify-center gap-4" aria-label="Pages">
      <Button size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        {newer}
      </Button>
      <span className="text-sm text-gray-400">
        Page {page} of {pages}
      </span>
      <Button size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        {older}
      </Button>
    </nav>
  );
}
