// The transient notices, floating above whatever page is shown: a banner for
// a problem, a toast for a change done, with its one action (Undo).
import { Banner, LinkButton, Toast } from "../design/kit";
import { useNoticesStore } from "../store/notices";

export function Notices() {
  const notices = useNoticesStore((s) => s.notices);
  const dismiss = useNoticesStore((s) => s.dismiss);
  const act = useNoticesStore((s) => s.act);
  if (notices.length === 0) return null;
  return (
    <div className="acme-toasts">
      {notices.map((notice) =>
        notice.tone === "done" ? (
          <Toast
            key={notice.id}
            action={notice.action?.label}
            onAction={() => act(notice.id)}
            onDismiss={() => dismiss(notice.id)}
          >
            {notice.message}
          </Toast>
        ) : (
          <Banner key={notice.id}>
            {notice.message} <LinkButton onClick={() => dismiss(notice.id)}>dismiss</LinkButton>
          </Banner>
        ),
      )}
    </div>
  );
}
