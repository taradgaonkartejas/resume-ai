import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { restrictToVerticalAxis } from "@dnd-kit/modifiers";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical } from "lucide-react";
import { cn } from "cn";
import { BulletRow } from "./fields";
import { useStableIds } from "./useStableIds";

/**
 * Drag-reorderable bullet list (mouse + keyboard), nested INSIDE the
 * entry-level DndContext of a sortable card. dnd-kit handles nesting by
 * scoping each list to its own DndContext; the two never see each other's
 * ids because bullets live under a per-entry `ownerKey` namespace.
 *
 * Every sortable surface in the editor (roles, projects, education, skill
 * groups, bullets) follows the same contract: ids and data receive the same
 * permutation in one handler, and the grip is a dedicated handle so text
 * selection inside the textarea is never a drag.
 */

function SortableBulletRow({
  sortableId,
  ...props
}: {
  sortableId: string;
} & React.ComponentProps<typeof BulletRow>) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: sortableId });

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn("relative", isDragging && "z-10 opacity-80")}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label="Reorder this point"
        className="absolute -left-1 top-2 inline-flex size-6 cursor-grab items-center
                   justify-center rounded-md text-content-muted
                   hover:bg-surface-3 hover:text-content active:cursor-grabbing
                   focus-visible:outline-2 focus-visible:outline-brand"
      >
        <GripVertical className="size-3.5" aria-hidden />
      </button>
      <div className="pl-5">
        <BulletRow {...props} />
      </div>
    </div>
  );
}

export function SortableBullets({
  ownerKey,
  bullets,
  focusRef,
  onChange,
}: {
  /** Unique per owning entry ("exp_0", "prj_1") so bullet ids never collide
      with the entry-level sortable's ids or a sibling entry's bullets. */
  ownerKey: string;
  bullets: string[];
  focusRef: string | null;
  onChange: (bullets: string[]) => void;
}) {
  const { ids, reorder } = useStableIds(bullets.length);
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
    // Ids and data must receive the SAME permutation or every subsequent
    // drag addresses the wrong bullet.
    reorder(from, to);
    const next = [...bullets];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onChange(next);
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      modifiers={[restrictToVerticalAxis]}
      onDragEnd={onDragEnd}
    >
      <SortableContext items={scopedIds} strategy={verticalListSortingStrategy}>
        <div className="space-y-2">
          {bullets.map((bullet, j) => (
            <SortableBulletRow
              key={scopedIds[j]}
              sortableId={scopedIds[j]}
              id={`f-${ownerKey}.bullet_${j}`}
              value={bullet}
              focus={focusRef === `${ownerKey}.bullet_${j}`}
              highlight={focusRef === `${ownerKey}.bullet_${j}`}
              onChange={(v) => onChange(bullets.map((b, k) => (k === j ? v : b)))}
              onRemove={() => onChange(bullets.filter((_, k) => k !== j))}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
