// Inicialización de Firebase (solo Auth). El apiKey de Firebase Web NO es secreto:
// es un identificador público del proyecto; la seguridad la da la verificación del token
// en el backend (firebase-admin) + las reglas. Por eso va en el frontend sin problema.
import { initializeApp } from "firebase/app";
import { getAuth, GoogleAuthProvider } from "firebase/auth";

const firebaseConfig = {
  apiKey: "AIzaSyCryi0IJ7lHG2yeroBpPxRh3vQGYkzKCG4",
  authDomain: "contabu-prod.firebaseapp.com",
  projectId: "contabu-prod",
  storageBucket: "contabu-prod.firebasestorage.app",
  messagingSenderId: "598143152807",
  appId: "1:598143152807:web:79d57b96f3d19e7baba182",
  measurementId: "G-N2JV9RDMCL",
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const googleProvider = new GoogleAuthProvider();
