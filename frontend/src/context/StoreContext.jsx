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

  // --- Helper: standard auth header
  const authHeaders = token ? { headers: { token } } : {};

  // --- Normalize cart payloads into a { [foodId]: qty } map
  const extractCartMap = (payload) => {
    if (!payload || typeof payload !== 'object') return {};
    // Typical Node template
    if (payload.cartData && typeof payload.cartData === 'object') return payload.cartData;
    // Alt nesting
    if (payload.data?.cartData && typeof payload.data.cartData === 'object') return payload.data.cartData;
    // Some APIs return { items: [{_id, qty}] }
    if (Array.isArray(payload.items)) {
      const out = {};
      for (const it of payload.items) {
        const id = it?._id || it?.itemId || it?.id;
        const q  = Number(it?.qty ?? it?.quantity ?? 0);
        if (id && q > 0) out[id] = (out[id] || 0) + q;
      }
      return out;
    }
    return {};
  };

  // --- Refresh cart from backend (idempotent)
  const refreshCart = async () => {
    if (!token || !url) return;
    try {
      // Your API uses POST /api/cart/get with header { token }
      const res = await axios.post(`${url}/api/cart/get`, {}, authHeaders);
      const ok = !!(res.data?.success ?? true); // be lenient if backend omits success
      if (ok) {
        const map = extractCartMap(res.data) || {};
        setCartItems(map);
      }
    } catch (e) {
      console.error('refreshCart failed', e);
    }
  };

  const addToCart = async (itemId) => {
    setCartItems((prev) => ({ ...prev, [itemId]: (prev[itemId] || 0) + 1 }));
    if (token && url) {
      try {
        await axios.post(`${url}/api/cart/add`, { itemId }, authHeaders);
      } catch (e) {
        console.error('addToCart failed', e);
        // soft rollback optional
      }
    }
  };

  const removeFromCart = async (itemId) => {
    setCartItems((prev) => ({ ...prev, [itemId]: Math.max(0, (prev[itemId] || 0) - 1) }));
    if (token && url) {
      try {
        await axios.post(`${url}/api/cart/remove`, { itemId }, authHeaders);
      } catch (e) {
        console.error('removeFromCart failed', e);
      }
    }
  };

  // --- NEW: Clear entire cart (tries common endpoints)
  const clearCart = async () => {
    // optimistic UI
    setCartItems({});
    if (!token || !url) return;

    // Try likely endpoints in order; ignore failures until all fail
    const attempts = [
      () => axios.post(`${url}/api/cart/clear`, {}, authHeaders),
      () => axios.post(`${url}/api/cart/reset`, {}, authHeaders),
      () => axios.post(`${url}/api/cart/empty`, {}, authHeaders),
      // some templates accept DELETEs
      () => axios.delete(`${url}/api/cart/items`, authHeaders),
      () => axios.delete(`${url}/api/cart`, authHeaders),
      // fallback: set empty items map
      () => axios.post(`${url}/api/cart`, { items: {} }, authHeaders),
    ];

    let success = false;
    for (const call of attempts) {
      try {
        const res = await call();
        const ok = !!(res.data?.success ?? true);
        const map = extractCartMap(res.data);
        // Treat either explicit success or an empty map as success
        if (ok || Object.keys(map).length === 0) {
          success = true;
          break;
        }
      } catch {
        // try next
      }
    }
    if (!success) {
      // If server didn’t actually clear, re-sync from backend
      await refreshCart();
    }
    // also notify any listeners (optional; chat widget already dispatches this)
    window.dispatchEvent(new CustomEvent('cart:refresh'));
  };

  const getTotalCartValue = () => {
    let total = 0;
    for (const item in cartItems) {
      const qty = cartItems[item];
      if (qty > 0) {
        const info = food_list.find((p) => p._id === item);
        if (info) total += info.price * qty;
      }
    }
    return total;
  };

  const contextvalue = {
    food_list, cartItems, setCartItems,
    addToCart, removeFromCart, getTotalCartValue,
    url, token, setToken, search, setSearch, showSearch, setShowSearch,
    refreshCart,
    clearCart,            // <-- expose clearCart to UI
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
      if (res.data?.success) setCartItems(extractCartMap(res.data));
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
      } else {
        setCartItems({});
      }
    })();
  }, []);

  // --- DE-DUPED: single cart:refresh listener that re-syncs from server
  useEffect(() => {
    const onRefresh = async () => {
      const t = localStorage.getItem('token');
      if (t) {
        await loadCartData(t);
      } else {
        setCartItems({});
      }
    };
    window.addEventListener('cart:refresh', onRefresh);
    return () => window.removeEventListener('cart:refresh', onRefresh);
  }, []);

  if (loading) return <div>Loading food items...</div>;

  return (
    <StoreContext.Provider value={contextvalue}>
      {props.children}
    </StoreContext.Provider>
  );
};

export default StoreContextProvider;