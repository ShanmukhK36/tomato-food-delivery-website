import Navbar from "./components/navbar/Navbar";
import Sidebar from "./components/sidebar/Sidebar";
import {Routes, Route} from 'react-router-dom';
import Add from "./pages/add/Add";
import List from "./pages/list/List";
import Orders from "./pages/orders/Orders";
 import { ToastContainer } from 'react-toastify';

function App() {
  const url = import.meta.env.VITE_BACKEND_URL;

  return (
    <div>
      <ToastContainer />
      <Navbar />
      <hr />
      <div className="app-content">
        <Sidebar />
        <Routes>
          <Route path="/add" element={<Add url={url}/>}/>
          <Route path="/list" element={<List url={url}/>}/>
          <Route path="/orders" element={<Orders url={url}/>}/>
        </Routes>
      </div>
    </div>
  )
}

export default App
