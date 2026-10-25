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
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { useStableIds } from "./useStableIds";

/**
 * One level of vertical drag-reorder around an arbitrary list — the Format
 * step's skill groups and education entries both need the exact machinery
 * ExperienceSection grew for whole roles (stable ids, handle-only drags,
 * matched id/data permutations). Factored out rather than copied a third
 * time.
 *
 * `count` is the length of the list being edited; the caller maps each item
 * to a stable id via `ids[i]` and applies the same permutation `onReorder`
 * reports to its own data array.
 */
export function SortableList({
  count,
  idPrefix,
  onReorder,
  children,
  className = "space-y-3",
}: {
  count: number;
  /** Namespace the stable ids so two lists on one page never collide. */
  idPrefix: string;
  onReorder: (from: number, to: number) => void;
  children: (ids: string[]) => React.ReactNode;
  className?: string;
}) {
  const { ids, reorder } = useStableIds(count);
  const scoped = ids.map((id) => `${idPrefix}.${id}`);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = scoped.indexOf(String(active.id));
    const to = scoped.indexOf(String(over.id));
    if (from < 0 || to < 0) return;
    // Ids and data must receive the SAME permutation: reorder() moves the
    // id list internally, onReorder lets the caller move the data.
    reorder(from, to);
    onReorder(from, to);
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      modifiers={[restrictToVerticalAxis]}
      onDragEnd={onDragEnd}
    >
      <SortableContext items={scoped} strategy={verticalListSortingStrategy}>
        <div className={className}>{children(scoped)}</div>
      </SortableContext>
    </DndContext>
  );
}
