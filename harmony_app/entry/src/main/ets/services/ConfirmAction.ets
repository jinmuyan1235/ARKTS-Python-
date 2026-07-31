export interface DangerConfirmation {
  title: string
  message: string
  confirmText: string
}

export function showDangerConfirmation(
  confirmation: DangerConfirmation,
  onConfirm: () => void
): void {
  AlertDialog.show({
    title: confirmation.title,
    message: confirmation.message,
    primaryButton: {
      value: '返回',
      action: () => {
      }
    },
    secondaryButton: {
      value: confirmation.confirmText,
      fontColor: '#B42318',
      action: onConfirm
    }
  })
}
