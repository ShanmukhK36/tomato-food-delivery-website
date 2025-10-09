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
    const addToCart = async (itemId) => {
        if(!cartItems[itemId]) {
            setCartItems((prev) => ({...prev,[itemId] : 1}));
        } else {
            setCartItems((prev) => ({...prev,[itemId] : prev[itemId] + 1}));
        }
        if(token) {
            await axios.post(url+'/api/cart/add', {itemId}, {headers: {token}});
        }
    }
    const removeFromCart = async (itemId) => {
        setCartItems((prev) => ({...prev,[itemId] : prev[itemId] - 1}));
        if(token) {
            await axios.post(url+'/api/cart/remove', {itemId}, {headers: {token}});
        }
    }
    const getTotalCartValue = () => {
        let totalAmount = 0;
        for(const item in cartItems) {
            if(cartItems[item] > 0) {
                let itemInfo = food_list.find((product) => product._id === item);
                totalAmount += itemInfo.price * cartItems[item];
            }
        }
        return totalAmount;
    }

    const contextvalue = {
        food_list, cartItems, setCartItems, addToCart, removeFromCart, getTotalCartValue, url, 
        token, setToken, search, setSearch, showSearch, setShowSearch
    }

    const fetchFoodList = async () => {
        try {
            const response = await axios.get(url+'/api/food/list');
            setFoodList(response.data.data);
        } catch(error) {
            console.error("Failed to fetch food list:", error);
        } finally {
            setLoading(false);
        }
        
    }

    const loadCartData = async (token) => {
        const response = await axios.post(url+'/api/cart/get', {}, {headers: {token}});
        if(response.data.success) {
            setCartItems(response.data.cartData);
        } else {
            console.log(response.data.message);
        }
        
    }

    useEffect(() => {
        async function loadData() {
            await fetchFoodList();
            if(localStorage.getItem('token')) {
                setToken(localStorage.getItem('token'));
                await loadCartData(localStorage.getItem('token'));
            }
        }
        loadData();
    }, []);
    if (loading) return <div>Loading food items...</div>;
  return (
    <StoreContext.Provider value={contextvalue}>
        {props.children}
    </StoreContext.Provider>
  )
}

export default StoreContextProvider;