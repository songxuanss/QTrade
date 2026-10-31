import { LoadingState } from "../../../components/ui/states";

/** Dynamic workspace routes need their own loading boundary. Without it,
 * Next waits for the full server payload before navigating, and link prefetch
 * cannot provide an immediate skeleton for section changes. */
export default function WorkspaceSectionLoading() {
  return (
    <div className="workspace-page workspace-route-loading">
      <LoadingState label="正在加载工作区页面" />
    </div>
  );
}
