import axios from 'axios';
import { createContext, useEffect, useState } from 'react';

export const StoreContext = createContext(null);

const StoreContextProvider = (props) => {
  const url = import.meta.env.VITE_BACKEND_URL;
  const [loading, setLoading] = useState(true);
  const [cartItems, setCartItems] = useState({});
  const [token, setToken] = useState('');
  const [food_list, setFoodList] = useState([]);
  const [search, setSearch] = useState('');
  const [showSearch, setShowSearch] = useState(false);

  const refreshCart = async () => {
    if (!token) return;
    try {
      const res = await axios.post(`${url}/api/cart/get`, {}, { headers: { token } });
      if (res.data?.success) setCartItems(res.data.cartData || {});
    } catch (e) {
      console.error('refreshCart failed', e);
    }
  };

  const addToCart = async (itemId) => {
    setCartItems((prev) => ({ ...prev, [itemId]: (prev[itemId] || 0) + 1 }));
    if (token) await axios.post(`${url}/api/cart/add`, { itemId }, { headers: { token } });
  };

  const removeFromCart = async (itemId) => {
    setCartItems((prev) => ({ ...prev, [itemId]: Math.max(0, (prev[itemId] || 0) - 1) }));
    if (token) await axios.post(`${url}/api/cart/remove`, { itemId }, { headers: { token } });
  };

  const getTotalCartValue = () => {
    let total = 0;
    for (const item in cartItems) {
      if (cartItems[item] > 0) {
        const info = food_list.find((p) => p._id === item);
        if (info) total += info.price * cartItems[item];
      }
    }
    return total;
  };

  const contextvalue = {
    food_list, cartItems, setCartItems,
    addToCart, removeFromCart, getTotalCartValue,
    url, token, setToken, search, setSearch, showSearch, setShowSearch,
    refreshCart,                     // <-- export it
  };

  const fetchFoodList = async () => {
    try {
      const response = await axios.get(`${url}/api/food/list`);
      setFoodList(response.data.data);
    } catch (error) {
      console.error('Failed to fetch food list:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadCartData = async (tok) => {
    try {
      const res = await axios.post(`${url}/api/cart/get`, {}, { headers: { token: tok } });
      if (res.data.success) setCartItems(res.data.cartData);
    } catch (e) {
      console.error('loadCartData failed', e);
    }
  };

  useEffect(() => {
    (async () => {
      await fetchFoodList();
      const tok = localStorage.getItem('token');
      if (tok) {
        setToken(tok);
        await loadCartData(tok);
      }
    })();
  }, []);

  useEffect(() => {
   const onRefresh = async () => {
     const t = localStorage.getItem('token');
     if (t) {
       await loadCartData(t);
     }
   };
   window.addEventListener('cart:refresh', onRefresh);
   return () => window.removeEventListener('cart:refresh', onRefresh);
 }, []);

  // Listen for chat-triggered refresh events
  useEffect(() => {
    const handler = () => refreshCart();
    window.addEventListener('cart:refresh', handler);
    return () => window.removeEventListener('cart:refresh', handler);
  }, [token]);

  if (loading) return <div>Loading food items...</div>;

  return (
    <StoreContext.Provider value={contextvalue}>
      {props.children}
    </StoreContext.Provider>
  );
};

export default StoreContextProvider;