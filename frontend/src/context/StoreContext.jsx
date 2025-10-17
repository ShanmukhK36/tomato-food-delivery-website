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
    if (!payload || typeof payload !== "object") return {};

    let map = {};

    if (payload.cartData && typeof payload.cartData === "object") {
      map = payload.cartData;
    } else if (payload.data?.cartData && typeof payload.data.cartData === "object") {
      map = payload.data.cartData;
    } else if (Array.isArray(payload.items)) {
      const out = {};
      for (const it of payload.items) {
        const id = it?._id || it?.itemId || it?.id;
        const q = Number(it?.qty ?? it?.quantity ?? 0);
        if (id && q > 0) out[id] = (out[id] || 0) + q;
      }
      map = out;
    }
    const cleaned = Object.fromEntries(
      Object.entries(map || {}).filter(([_, qty]) => Number(qty) > 0)
    );

    return cleaned;
  };

  // --- Refresh cart from backend (idempotent)
  const refreshCart = async () => {
    if (!token || !url) return;
    try {
      const res = await axios.post(`${url}/api/cart/get`, {}, authHeaders);
      const ok = !!(res.data?.success ?? true);
      if (ok) setCartItems(extractCartMap(res.data) || {});
    } catch (e) {
      console.error('refreshCart failed', e);
    }
  };

  // === Single-item ops (unchanged) ===
  const addToCart = async (itemId) => {
    setCartItems((prev) => ({ ...prev, [itemId]: (prev[itemId] || 0) + 1 }));
    if (token && url) {
      try {
        await axios.post(`${url}/api/cart/add`, { itemId }, authHeaders);
      } catch (e) {
        console.error('addToCart failed', e);
        // optional: await refreshCart();
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
        // optional: await refreshCart();
      }
    }
  };

  // === NEW: Multi-item ops (for chatbot & batch UI) ===
  // items: Array<{ itemId: string, qty: number }>
  const addManyToCart = async (items = []) => {
    if (!Array.isArray(items) || items.length === 0) return;
    // optimistic
    setCartItems((prev) => {
      const next = { ...prev };
      for (const it of items) {
        const id = it?.itemId;
        const q  = Math.max(1, Number(it?.qty ?? 1));
        if (!id) continue;
        next[id] = (next[id] || 0) + q;
      }
      return next;
    });
    if (token && url) {
      try {
        await axios.post(`${url}/api/cart/add-many`, { items }, authHeaders);
      } catch (e) {
        console.error('addManyToCart failed', e);
        await refreshCart();
      }
    }
    // Let listeners (e.g., chat widget) know the cart may have changed
    window.dispatchEvent(new CustomEvent('cart:refresh'));
  };

  // items: Array<{ itemId: string, qty: number }>
  const removeManyFromCart = async (items = []) => {
    if (!Array.isArray(items) || items.length === 0) return;
    // optimistic
    setCartItems((prev) => {
      const next = { ...prev };
      for (const it of items) {
        const id = it?.itemId;
        const q  = Math.max(1, Number(it?.qty ?? 1));
        if (!id) continue;
        const cur = next[id] || 0;
        next[id] = Math.max(0, cur - q);
        if (next[id] === 0) delete next[id];
      }
      return next;
    });
    if (token && url) {
      try {
        await axios.post(`${url}/api/cart/remove-many`, { items }, authHeaders);
      } catch (e) {
        console.error('removeManyFromCart failed', e);
        await refreshCart();
      }
    }
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
    addToCart, removeFromCart,           // single-item
    addManyToCart, removeManyFromCart,   // multi-item
    getTotalCartValue,
    url, token, setToken, search, setSearch, showSearch, setShowSearch,
    refreshCart,
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
      if (res.data?.success ?? true) setCartItems(extractCartMap(res.data));
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

  // Re-sync cart whenever something (like the chatbot) dispatches 'cart:refresh'
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