export type FlipRect = {
	left: number;
	top: number;
	width?: number;
	height?: number;
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

	return { left, top, width: element.offsetWidth, height: element.offsetHeight };
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
	reducedMotion: boolean,
	animateWidth = false
): Animation | null {
	const delta = flipDelta(previous, next);
	const scaleX =
		animateWidth && previous.width && next.width && next.width > 0
			? previous.width / next.width
			: 1;
	const widthChanged = animateWidth && Math.abs(scaleX - 1) >= 0.005;
	if (!shouldAnimateFlip(delta, reducedMotion) && (reducedMotion || !widthChanged)) return null;
	const fromTransform = `translate3d(${delta.x}px, ${delta.y}px, 0)`;
	const keyframes = animateWidth
		? [
				{ transform: `${fromTransform} scaleX(${scaleX})`, transformOrigin: 'top left' },
				{ transform: 'translate3d(0, 0, 0) scaleX(1)', transformOrigin: 'top left' }
			]
		: [{ transform: fromTransform }, { transform: 'translate3d(0, 0, 0)' }];

	return element.animate(
		keyframes,
		{
			duration: 400,
			easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
			fill: 'both'
		}
	);
}
