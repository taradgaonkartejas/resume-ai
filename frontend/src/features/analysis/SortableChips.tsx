import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  rectSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical } from "lucide-react";
import { cn } from "cn";
import { useStableIds } from "./useStableIds";

/**
 * Drag-reorderable chips inside a Skills group.
 *
 * Chips wrap in a flex row, so unlike the vertical lists (SortableBullets,
 * SortableList) this uses rectSortingStrategy and no axis restriction: a
 * wrapped chip is just as much "down" as "over" and constraining the motion
 * to Y made wrapped rows feel broken.
 *
 * As everywhere in this editor, ids and data receive ONE permutation in a
 * single handler -- see useStableIds.
 */

function SortableChip({
  sortableId,
  label,
  onRemove,
}: {
  sortableId: string;
  label: string;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: sortableId });

  return (
    <span
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-surface-3 py-0.5 pl-1.5 pr-1.5 text-xs",
        isDragging && "z-10 opacity-80 shadow-lg",
      )}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label={`Reorder ${label}`}
        className="inline-flex size-4 cursor-grab items-center justify-center
                   rounded text-content-muted hover:text-content
                   active:cursor-grabbing focus-visible:outline-2
                   focus-visible:outline-brand"
      >
        <GripVertical className="size-3" aria-hidden />
      </button>
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${label}`}
        className="-mr-0.5 rounded-full px-1 text-content-muted hover:text-reject-text"
      >
        ×
      </button>
    </span>
  );
}

export function SortableChips({
  ownerKey,
  items,
  onChange,
  onRemove,
}: {
  /** Namespace the ids, e.g. "skillgroup_1", so sibling groups never collide. */
  ownerKey: string;
  items: string[];
  onChange: (items: string[]) => void;
  onRemove: (index: number) => void;
}) {
  const { ids, reorder } = useStableIds(items.length);
  const scopedIds = ids.map((id) => `${ownerKey}.${id}`);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = scopedIds.indexOf(String(active.id));
    const to = scopedIds.indexOf(String(over.id));
    if (from < 0 || to < 0) return;
    reorder(from, to);
    const next = [...items];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onChange(next);
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={onDragEnd}
    >
      <SortableContext items={scopedIds} strategy={rectSortingStrategy}>
        <div className="flex flex-wrap gap-1.5">
          {items.map((item, j) => (
            <SortableChip
              key={scopedIds[j]}
              sortableId={scopedIds[j]}
              label={item}
              onRemove={() => onRemove(j)}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
