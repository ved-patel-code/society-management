import { useMemo } from "react";
import DOMPurify from "dompurify";
import { cn } from "@/lib/utils";

interface NoticeBodyProps {
  html: string;
  className?: string;
}

// Server-side already sanitizes NoticeDetail.body; we re-sanitize client-side as
// defense-in-depth against the exact allow-list agreed with the backend.
const ALLOWED_TAGS = [
  "p", "br", "span", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li", "a",
  "h1", "h2", "h3", "h4", "blockquote", "code", "pre", "hr",
];
const ALLOWED_ATTR = ["href", "title"];

// Force every surviving <a> to open safely in a new tab. Registered once at module load.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

/**
 * Renders server-sanitized notice HTML. The body is re-sanitized here before it
 * ever reaches dangerouslySetInnerHTML, so a stray <script>/onclick can never run.
 */
export function NoticeBody({ html, className }: NoticeBodyProps) {
  const clean = useMemo(
    () =>
      DOMPurify.sanitize(html ?? "", {
        ALLOWED_TAGS,
        ALLOWED_ATTR,
      }),
    [html],
  );

  return (
    <div
      className={cn(
        "max-w-prose space-y-3 text-sm leading-relaxed break-words",
        "[&_a]:font-medium [&_a]:text-primary [&_a]:underline [&_a]:underline-offset-4",
        "[&_h1]:text-xl [&_h1]:font-semibold [&_h2]:text-lg [&_h2]:font-semibold",
        "[&_h3]:text-base [&_h3]:font-semibold [&_h4]:text-sm [&_h4]:font-semibold",
        "[&_ul]:list-disc [&_ul]:pl-6 [&_ol]:list-decimal [&_ol]:pl-6 [&_li]:my-1",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-4 [&_blockquote]:text-muted-foreground",
        "[&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3 [&_pre]:text-xs",
        "[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs",
        "[&_hr]:my-4 [&_hr]:border-border",
        className,
      )}
      // eslint-disable-next-line react/no-danger -- content is DOMPurify-sanitized above
      dangerouslySetInnerHTML={{ __html: clean }}
    />
  );
}
