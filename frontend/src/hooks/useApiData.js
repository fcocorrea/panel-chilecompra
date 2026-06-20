import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../api/client'

export function useApiData(fetchFn, deps = []) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const cargar = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const resultado = await fetchFn()
      setData(resultado)
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err
          : new ApiError(err?.message || 'Error desconocido', -1, null),
      )
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    cargar()
  }, [cargar])

  return { data, loading, error, refetch: cargar }
}
