import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical } from "lucide-react";
import { cn } from "cn";

/**
 * One draggable experience card.
 *
 * Why a library at all, for a list of 2-6 items: the part that is tedious to
 * hand-roll is not the dragging, it is making the dragging operable without a
 * mouse. dnd-kit's KeyboardSensor gives arrow-key reordering and the live
 * announcements a screen reader needs, and shipping mouse-only drag would be
 * worse than shipping no drag at all.
 *
 * The grip is a dedicated handle rather than the whole card being draggable:
 * these cards are full of text inputs, and a card-wide drag listener competes
 * with selecting text in them.
 */
export function SortableRole({
  id,
  children,
}: {
  id: string;
  children: React.ReactNode;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id });

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "relative rounded-xl border border-line-soft bg-surface-2 p-3",
        // Lift the dragged card above its neighbours; without the z-index it
        // slides *under* the cards it is passing.
        isDragging && "z-10 opacity-80 shadow-lg",
      )}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label="Reorder this role"
        className="absolute right-2 top-2 inline-flex size-7 cursor-grab items-center
                   justify-center rounded-md text-content-muted
                   hover:bg-surface-3 hover:text-content active:cursor-grabbing
                   focus-visible:outline-2 focus-visible:outline-brand"
      >
        <GripVertical className="size-4" aria-hidden />
      </button>
      {children}
    </div>
  );
}
