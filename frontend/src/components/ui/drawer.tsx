import * as React from 'react'
import { Drawer as DrawerPrimitive } from 'vaul'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

const Drawer = DrawerPrimitive.Root
const DrawerTrigger = DrawerPrimitive.Trigger
const DrawerClose = DrawerPrimitive.Close

const DrawerContent = React.forwardRef<
  React.ComponentRef<typeof DrawerPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DrawerPrimitive.Content> & { title?: string }
>(({ className, children, title, ...props }, ref) => (
  <DrawerPrimitive.Portal>
    <DrawerPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60" />
    <DrawerPrimitive.Content
      ref={ref}
      className={cn(
        'fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-line bg-bg-1 outline-none',
        className,
      )}
      {...props}
    >
      <div className="flex shrink-0 items-center justify-between border-b border-line px-4 py-3">
        <DrawerPrimitive.Title className="font-mono text-[13px] text-ink-0">
          {title ?? '面板'}
        </DrawerPrimitive.Title>
        <DrawerPrimitive.Close className="rounded-md p-1.5 text-ink-2 transition-colors hover:bg-bg-2 hover:text-ink-0" aria-label="关闭">
          <X className="size-4" />
        </DrawerPrimitive.Close>
      </div>
      <div className="flex-1 overflow-y-auto p-4">{children}</div>
    </DrawerPrimitive.Content>
  </DrawerPrimitive.Portal>
))
DrawerContent.displayName = 'DrawerContent'

export { Drawer, DrawerTrigger, DrawerClose, DrawerContent }
