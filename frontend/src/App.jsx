import { useState } from 'react'
import Navbar from './components/Navbar'
import { Route, Routes } from 'react-router-dom'
import Home from './pages/Home'
import Cart from './pages/Cart'
import Footer from './components/Footer'
import LoginPopup from './components/LoginPopup'
import PlaceOrder from './pages/PlaceOrder'
import Verify from './pages/Verify'
import MyOrders from './pages/MyOrders'
import SearchBar from './components/SearchBar'
import ChatbotWidget from './components/ChatbotWidget'

const App = () => {
  const [showLogin, setShowLogin] = useState(false);
  return (
    <div>
      {showLogin ? <LoginPopup setShowLogin={setShowLogin} /> : <></>}
      <div className='w-[90%] sm:w-[80%] m-auto sm:px-[5vm] md:px-[7vm] lg:px-[9vm]'>
        <Navbar setShowLogin={setShowLogin}/>
        <SearchBar />
        <Routes>
          <Route path='/' element={<Home />}/>
          <Route path='/cart' element={<Cart />}/>
          <Route path='/order' element={<PlaceOrder />}/>
          <Route path='/verify' element={<Verify />}/>
          <Route path='/myorders' element={<MyOrders />}/>
        </Routes>
      </div>
      {/* Floating chat lives outside main container so it can be fixed-positioned */}
      <ChatbotWidget />
      <Footer />
    </div>
  )
}

export default App
