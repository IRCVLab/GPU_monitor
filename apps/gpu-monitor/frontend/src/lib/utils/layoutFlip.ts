export type FlipRect = {
	left: number;
	top: number;
};

export type FlipDelta = {
	x: number;
	y: number;
};

export function documentRect(element: HTMLElement): FlipRect {
	let left = 0;
	let top = 0;
	let current: HTMLElement | null = element;

	while (current) {
		left += current.offsetLeft;
		top += current.offsetTop;
		current = current.offsetParent instanceof HTMLElement ? current.offsetParent : null;
	}

	return { left, top };
}

export function flipDelta(previous: FlipRect, next: FlipRect): FlipDelta {
	return {
		x: previous.left - next.left,
		y: previous.top - next.top
	};
}

export function shouldAnimateFlip(delta: FlipDelta, reducedMotion: boolean): boolean {
	return !reducedMotion && (Math.abs(delta.x) >= 0.5 || Math.abs(delta.y) >= 0.5);
}

export function animateFlip(
	element: HTMLElement,
	previous: FlipRect,
	next: FlipRect,
	reducedMotion: boolean
): Animation | null {
	const delta = flipDelta(previous, next);
	if (!shouldAnimateFlip(delta, reducedMotion)) return null;

	return element.animate(
		[
			{ transform: `translate3d(${delta.x}px, ${delta.y}px, 0)` },
			{ transform: 'translate3d(0, 0, 0)' }
		],
		{
			duration: 400,
			easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
			fill: 'both'
		}
	);
}
