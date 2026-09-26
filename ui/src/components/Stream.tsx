import { useEffect, useRef } from "react";

/** The camera's MJPEG stream.
 *
 *  The source is blanked on unmount: removing an <img> does not reliably abort
 *  an MJPEG request, and a lingering one holds one of the browser's six
 *  connections to this host open for good. Mount it only while the camera is on.
 */
export function Stream({ className, alt }: { className?: string; alt: string }) {
  const img = useRef<HTMLImageElement>(null);
  useEffect(() => {
    const el = img.current;
    return () => { if (el) el.src = "data:,"; };
  }, []);
  return <img ref={img} className={className} src="/video" alt={alt} />;
}
