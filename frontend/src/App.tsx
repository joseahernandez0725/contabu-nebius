import { useEffect, useState } from 'react'

function App() {
  // "Estado" = datos que la pantalla recuerda y muestra. Aquí: el mensaje y un posible error.
  const [mensaje, setMensaje] = useState<string>('Cargando...')
  const [error, setError] = useState<string | null>(null)

  // useEffect con [] al final = se ejecuta UNA sola vez, cuando la pantalla aparece.
  // Aquí hacemos la petición HTTP al backend (igual que invocar una función remota).
  useEffect(() => {
    fetch('http://localhost:8000/api/hello')
      .then((res) => res.json())            // convertir la respuesta JSON a objeto
      .then((data) => setMensaje(data.message)) // guardar el mensaje en el estado
      .catch((err) =>
        setError('No se pudo conectar con el backend: ' + err.message)
      )
  }, [])

  return (
    <main style={{ fontFamily: 'sans-serif', textAlign: 'center', padding: '3rem' }}>
      <h1>ContaBu</h1>
      <p>Mensaje recibido del backend:</p>
      {error ? (
        <p style={{ color: 'crimson' }}>{error}</p>
      ) : (
        <p style={{ fontSize: '1.5rem' }}>{mensaje}</p>
      )}
    </main>
  )
}

export default App
