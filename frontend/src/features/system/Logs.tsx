import { Link, useSearchParams } from "react-router";
import { useLog } from "../../api/queries";
import { Segmented } from "../../components/Controls";
import { Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";

const LEVEL_STYLES: Record<string, string> = {
  info: "bg-gray-500",
  warning: "bg-yellow-400",
  error: "bg-red-500",
};

export function Logs() {
  useTitle("Logs");
  const [params, setParams] = useSearchParams();
  const level = params.get("level") ?? "";
  const { data, isPending, error } = useLog(level);
  return (
    <>
      <PageHeader title="Logs" subtitle="Newest first, kept for 60 days." />
      <div className="mb-6">
        <Segmented
          label="Level"
          value={level}
          onChange={(value) => setParams(value ? { level: value } : {})}
          options={[
            { value: "", label: "All" },
            { value: "warning", label: "Warnings and errors" },
            { value: "error", label: "Errors" },
          ]}
        />
      </div>
      {isPending ? (
        <Loading />
      ) : error || !data ? (
        <Notice tone="red">{error?.message ?? "Cannot load the logs"}</Notice>
      ) : data.rows.length ? (
        <Card className="divide-y divide-gray-700/70">
          {data.rows.map((row) => (
            <div key={row.id} className="log-row flex gap-3 px-4 py-2.5 text-sm" data-level={row.level}>
              <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${LEVEL_STYLES[row.level]}`} aria-label={row.level} title={row.level} />
              <span className="w-36 shrink-0 text-xs leading-6 text-gray-500">{timeAgo(row.at)}</span>
              <span className="min-w-0 flex-1 break-words text-gray-200">
                {row.message}
                {row.title && row.item_key && (
                  <>
                    {" "}
                    <Link to={`/item/${row.item_key}`} className="text-gray-400 hover:text-gray-200 hover:underline">
                      {row.title}
                    </Link>
                  </>
                )}
              </span>
            </div>
          ))}
        </Card>
      ) : (
        <Empty title="No entries" />
      )}
    </>
  );
}
