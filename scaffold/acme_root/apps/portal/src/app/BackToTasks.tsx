// The way back from Settings to the task list, above the page's title.
import { Link } from "react-router-dom";

export function BackToTasks() {
  return (
    <Link to="/" aria-label="Back to Tasks">
      <span aria-hidden="true">←</span> Tasks
    </Link>
  );
}
