import { ButtonLink } from "../../components/Button";
import { Empty } from "../../components/ui";
import { useTitle } from "../../lib/useTitle";

export function NotFound() {
  useTitle("Not found");
  return (
    <div className="mt-16">
      <Empty title="Page not found" action={<ButtonLink to="/">Go to the dashboard</ButtonLink>} />
    </div>
  );
}
