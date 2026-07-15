export type DashboardNetwork = 'internal' | 'external' | 'all';

export type DashboardShortcut =
	| { type: 'toggle-view' }
	| { type: 'select-network'; tab: DashboardNetwork }
	| { type: 'toggle-theme' };

type ShortcutKeyboardEvent = Pick<
	KeyboardEvent,
	| 'altKey'
	| 'code'
	| 'ctrlKey'
	| 'defaultPrevented'
	| 'isComposing'
	| 'metaKey'
	| 'repeat'
	| 'shiftKey'
	| 'target'
>;

type EditableTarget = {
	tagName?: string;
	isContentEditable?: boolean;
	closest?: (selector: string) => unknown;
};

function isEditingTarget(target: EventTarget | null): boolean {
	if (!target || typeof target !== 'object') return false;
	const element = target as EditableTarget;
	const tagName = element.tagName?.toUpperCase();
	if (tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT') return true;
	if (element.isContentEditable) return true;
	return Boolean(element.closest?.('[contenteditable]:not([contenteditable="false"])'));
}

const networkByCode: Readonly<Record<string, DashboardNetwork>> = {
	Digit1: 'internal',
	Numpad1: 'internal',
	Digit2: 'external',
	Numpad2: 'external',
	Digit3: 'all',
	Numpad3: 'all'
};

export function resolveDashboardShortcut(event: ShortcutKeyboardEvent): DashboardShortcut | null {
	if (
		event.defaultPrevented ||
		event.repeat ||
		event.isComposing ||
		event.metaKey ||
		event.ctrlKey ||
		event.altKey ||
		isEditingTarget(event.target)
	) {
		return null;
	}

	if (event.code === 'KeyV') return { type: 'toggle-view' };
	if (event.code === 'KeyC') return { type: 'toggle-theme' };

	const tab = event.shiftKey ? undefined : networkByCode[event.code];
	return tab ? { type: 'select-network', tab } : null;
}
