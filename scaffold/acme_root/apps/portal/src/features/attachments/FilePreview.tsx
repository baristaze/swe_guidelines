import { useEffect, useState } from "react";
import { Muted } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { usePreviewUrl } from "../../queries/attachments";
import type { PreviewKind } from "./attachmentsModel";

/** A file shown in the page: an image as a thumbnail that opens larger, a
 * video and a sound in the browser's player, a PDF in a frame. */
export function FilePreview({ fileId, kind, name }: { fileId: string; kind: PreviewKind; name: string }) {
  const url = usePreviewUrl(fileId, true);
  const [large, setLarge] = useState(false);
  useEffect(() => {
    if (!large) return;
    const close = (event: KeyboardEvent) => event.key === "Escape" && setLarge(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [large]);
  if (!url.data) return <Muted style={{ fontSize: tokens.font.size.sm }}>{url.isError ? "No preview" : "Loading preview"}</Muted>;
  const frame = { maxWidth: "100%", borderRadius: tokens.radius.sm, border: `1px solid ${tokens.color.border}` };
  if (kind === "image") {
    return (
      <>
        <button type="button" aria-label={`Open ${name}`} onClick={() => setLarge(true)} style={{ padding: 0, border: 0, background: "none", cursor: "zoom-in", justifySelf: "start" }}>
          <img src={url.data} alt={name} style={{ ...frame, display: "block", maxHeight: 120 }} />
        </button>
        {large ? (
          <div
            role="dialog"
            aria-label={name}
            onClick={() => setLarge(false)}
            style={{ position: "fixed", inset: 0, zIndex: 50, display: "grid", placeItems: "center", background: "rgba(0,0,0,0.72)", cursor: "zoom-out" }}
          >
            <img src={url.data} alt={name} style={{ maxWidth: "90vw", maxHeight: "90vh", borderRadius: tokens.radius.md, boxShadow: tokens.shadow.lg }} />
          </div>
        ) : null}
      </>
    );
  }
  if (kind === "video") return <video controls preload="metadata" src={url.data} aria-label={name} style={{ ...frame, width: 480 }} />;
  if (kind === "audio") return <audio controls preload="metadata" src={url.data} aria-label={name} style={{ maxWidth: "100%" }} />;
  return <iframe src={url.data} title={name} style={{ ...frame, width: "100%", height: 360 }} />;
}
