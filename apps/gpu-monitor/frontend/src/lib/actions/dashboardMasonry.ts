import type { DashboardLayoutWidth } from '$lib/stores/dashboardPrefs';
import { countResolvedGridTracks, placeOrderedMasonryItems } from '$lib/utils/orderedMasonry';
import {
	animateFlip,
	documentRect,
	type FlipRect
} from '$lib/utils/layoutFlip';

type MasonryOptions = {
	enabled: boolean;
	layoutWidth: DashboardLayoutWidth;
};

export function masonry(node: HTMLDivElement, initialOptions: MasonryOptions) {
	let frame = 0;
	let enabled = initialOptions.enabled;
	let layoutWidth = initialOptions.layoutWidth;
	let responsiveLayoutInvalidated = false;
	let itemObserver: ResizeObserver | null = null;
	let previousRects = new Map<HTMLElement, FlipRect>();
	let assignedColumns = new Map<HTMLElement, number>();
	let measuredHeights = new Map<HTMLElement, number>();
	let assignedItems: HTMLElement[] = [];
	let assignedColumnCount = 0;
	const layoutAnimations = new Map<HTMLElement, Animation>();
	const activeHeightTransitions = new Set<EventTarget>();
	const containerObserver = new ResizeObserver(() => schedule());
	const mutationObserver = new MutationObserver(() => observeItems());

	function schedule(): void {
		if (frame !== 0) return;
		frame = requestAnimationFrame(layout);
	}

	function rowSize(styles: CSSStyleDeclaration): number {
		const value = Number.parseFloat(styles.getPropertyValue('--monitor-dashboard-masonry-row'));
		return Number.isFinite(value) && value > 0 ? value : 8;
	}

	function rowGap(styles: CSSStyleDeclaration): number {
		const value = Number.parseFloat(styles.rowGap);
		return Number.isFinite(value) && value >= 0 ? value : 16;
	}

	function masonryItemBorderBoxBlockSize(child: HTMLElement, entry?: ResizeObserverEntry): number {
		const borderBoxSize = entry?.borderBoxSize as ResizeObserverSize | readonly ResizeObserverSize[] | undefined;
		const firstBorderBoxSize = Array.isArray(borderBoxSize) ? borderBoxSize[0] : borderBoxSize;
		const blockSize = firstBorderBoxSize?.blockSize;
		return Number.isFinite(blockSize) && blockSize > 0 ? blockSize : child.getBoundingClientRect().height;
	}

	function clearPlacement(child: HTMLElement): void {
		child.style.removeProperty('grid-column-start');
		child.style.removeProperty('grid-row-start');
		child.style.removeProperty('grid-row-end');
	}

	function clearAssignments(): void {
		assignedColumns.clear();
		assignedItems = [];
		assignedColumnCount = 0;
	}

	function clearMasonryState(): void {
		clearAssignments();
		measuredHeights.clear();
	}

	function isHeightTransition(event: TransitionEvent): boolean {
		return event.propertyName === 'grid-template-rows';
	}

	function handleHeightTransitionRun(event: TransitionEvent): void {
		if (!isHeightTransition(event) || !event.target) return;
		activeHeightTransitions.add(event.target);
	}

	function handleHeightTransitionSettled(event: TransitionEvent): void {
		if (!isHeightTransition(event) || !event.target) return;
		activeHeightTransitions.delete(event.target);
		schedule();
	}

	function layout(): void {
		frame = 0;
		const items = Array.from(node.children).filter(
			(child): child is HTMLElement => child instanceof HTMLElement
		);
		const visualRects = new Map(
			items.map((child) => {
				const rect = child.getBoundingClientRect();
				return [
					child,
					{
						left: rect.left + window.scrollX,
						top: rect.top + window.scrollY,
						width: rect.width,
						height: rect.height
					}
				];
			})
		);
		const animateResponsiveResize = responsiveLayoutInvalidated;

		if (responsiveLayoutInvalidated) {
			clearAssignments();
			measuredHeights.clear();
			for (const child of items) clearPlacement(child);
			responsiveLayoutInvalidated = false;
		}

		if (enabled) {
			const styles = getComputedStyle(node);
			const currentColumnCount = countResolvedGridTracks(styles.gridTemplateColumns);
			const columnCountChanged = currentColumnCount !== assignedColumnCount;
			const structureChanged =
				columnCountChanged ||
				items.length !== assignedItems.length ||
				items.some((child, index) => child !== assignedItems[index]);

			if (structureChanged) clearAssignments();
			if (columnCountChanged) measuredHeights.clear();

			const currentRowSize = rowSize(styles);
			const currentGap = rowGap(styles);
			const spans = items.map((child) => {
				const height = measuredHeights.get(child) ?? masonryItemBorderBoxBlockSize(child);
				measuredHeights.set(child, height);
				return Math.max(1, Math.ceil((height + currentGap) / (currentRowSize + currentGap)));
			});
			const placements = placeOrderedMasonryItems({
				columnCount: currentColumnCount,
				spans,
				preferredColumns: items.map((child) => assignedColumns.get(child) ?? null)
			});

			items.forEach((child, index) => {
				const placement = placements[index];
				assignedColumns.set(child, placement.gridColumnStart);
				child.style.gridColumnStart = String(placement.gridColumnStart);
				child.style.gridRowStart = String(placement.gridRowStart);
				child.style.gridRowEnd = placement.gridRowEnd;
			});
			assignedItems = items;
			assignedColumnCount = currentColumnCount;
		} else {
			clearMasonryState();
			for (const child of items) clearPlacement(child);
		}

		const nextRects = new Map(items.map((child) => [child, documentRect(child)]));
		const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
		const animateLayoutMoves = activeHeightTransitions.size === 0;
		const moves = items.flatMap((child) => {
			const previousFinal = previousRects.get(child);
			const next = nextRects.get(child);
			if (!previousFinal || !next) return [];
			if (
				Math.abs(previousFinal.left - next.left) < 0.5 &&
				Math.abs(previousFinal.top - next.top) < 0.5 &&
				(!animateResponsiveResize || Math.abs((previousFinal.width ?? 0) - (next.width ?? 0)) < 0.5)
			) return [];
			const previous = layoutAnimations.has(child) ? visualRects.get(child) ?? previousFinal : previousFinal;
			return [{ child, previous, next }];
		});

		if (!animateLayoutMoves) {
			for (const animation of layoutAnimations.values()) animation.cancel();
			layoutAnimations.clear();
			previousRects = nextRects;
			return;
		}

		if (moves.length > 0) {
			for (const animation of layoutAnimations.values()) animation.cancel();
			layoutAnimations.clear();
		}

		for (const { child, previous, next } of moves) {
			const animation = animateFlip(child, previous, next, reducedMotion, animateResponsiveResize);
			if (!animation) continue;
			layoutAnimations.set(child, animation);
			animation.addEventListener('finish', () => layoutAnimations.delete(child), { once: true });
			animation.addEventListener('cancel', () => layoutAnimations.delete(child), { once: true });
		}
		previousRects = nextRects;
	}

	function observeItems(): void {
		itemObserver?.disconnect();
		clearAssignments();
		const currentItems = Array.from(node.children).filter(
			(child): child is HTMLElement => child instanceof HTMLElement
		);
		const currentSet = new Set(currentItems);
		for (const child of Array.from(measuredHeights.keys())) {
			if (!currentSet.has(child)) measuredHeights.delete(child);
		}
		itemObserver = new ResizeObserver((entries) => {
			for (const entry of entries) {
				if (entry.target instanceof HTMLElement) {
					measuredHeights.set(entry.target, masonryItemBorderBoxBlockSize(entry.target, entry));
				}
			}
			schedule();
		});
		for (const child of currentItems) itemObserver.observe(child);
		schedule();
	}

	containerObserver.observe(node);
	mutationObserver.observe(node, { childList: true });
	node.addEventListener('transitionrun', handleHeightTransitionRun);
	node.addEventListener('transitionend', handleHeightTransitionSettled);
	node.addEventListener('transitioncancel', handleHeightTransitionSettled);
	observeItems();

	return {
		update(nextOptions: MasonryOptions) {
			const widthChanged = nextOptions.layoutWidth !== layoutWidth;
			if (enabled !== nextOptions.enabled) {
				clearMasonryState();
			}
			if (widthChanged) {
				responsiveLayoutInvalidated = true;
			}
			enabled = nextOptions.enabled;
			layoutWidth = nextOptions.layoutWidth;
			schedule();
		},
		destroy() {
			if (frame !== 0) cancelAnimationFrame(frame);
			node.removeEventListener('transitionrun', handleHeightTransitionRun);
			node.removeEventListener('transitionend', handleHeightTransitionSettled);
			node.removeEventListener('transitioncancel', handleHeightTransitionSettled);
			activeHeightTransitions.clear();
			for (const animation of layoutAnimations.values()) animation.cancel();
			layoutAnimations.clear();
			previousRects.clear();
			clearMasonryState();
			itemObserver?.disconnect();
			containerObserver.disconnect();
			mutationObserver.disconnect();
			for (const child of Array.from(node.children)) {
				if (child instanceof HTMLElement) clearPlacement(child);
			}
		}
	};
}
