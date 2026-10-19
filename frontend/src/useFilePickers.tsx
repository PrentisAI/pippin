import { useRef } from 'react'
import { fromInput, type UploadItem } from './upload'

/** Hidden pickers for files and for a folder; `pick('files' | 'folder')` opens one. */
export function useFilePickers(onItems: (items: UploadItem[]) => void) {
  const filesRef = useRef<HTMLInputElement>(null)
  const folderRef = useRef<HTMLInputElement>(null)
  const inputs = (
    <>
      <input ref={filesRef} type="file" multiple hidden
        onChange={(e) => { onItems(fromInput(e.target.files)); e.target.value = '' }} />
      <input ref={folderRef} type="file" hidden {...{ webkitdirectory: '', directory: '' }}
        onChange={(e) => { onItems(fromInput(e.target.files)); e.target.value = '' }} />
    </>
  )
  const pick = (what: 'files' | 'folder') => (what === 'files' ? filesRef : folderRef).current?.click()
  return { inputs, pick }
}
