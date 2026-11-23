import { Link, useSearchParams } from "react-router";
import { useHistory } from "../../api/queries";
import { Pager } from "../../components/Pager";
import { Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { useTitle } from "../../lib/useTitle";
import { HistoryList } from "./HistoryList";

export function History() {
  useTitle("History");
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") ?? "1") || 1;
  const { data, isPending, error } = useHistory(page);
  return (
    <>
      <PageHeader
        title="History"
        subtitle={
          <>
            Replaced files are kept in the archive and can be restored. Everything else is in the{" "}
            <Link to="/logs" className="text-indigo-300 hover:text-indigo-200">
              logs
            </Link>
            .
          </>
        }
      />
      {isPending ? (
        <Loading />
      ) : error || !data ? (
        <Notice tone="red">{error?.message ?? "Cannot load the history"}</Notice>
      ) : data.rows.length ? (
        <>
          <HistoryList rows={data.rows} linkTitles />
          <Pager page={data.page} pages={data.pages} newer="Newer" older="Older" onPage={(next) => setParams(next > 1 ? { page: String(next) } : {})} />
        </>
      ) : (
        <Empty title="No history yet" />
      )}
    </>
  );
}
