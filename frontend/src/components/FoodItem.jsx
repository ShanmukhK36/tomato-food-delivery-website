import { useContext } from 'react';
import { assets } from '../assets/assets';
import '../index.css';
import { StoreContext } from '../context/StoreContext';

const FoodItem = ({id, name, price, description, image}) => {
    const {cartItems, addToCart, removeFromCart, url} = useContext(StoreContext);
  return (
    <div className='w-full m-auto rounded-t-[15px] fade-in-items'>
        <div className='w-full relative'>
            <img className='w-full rounded-t-[15px]' src={url+'/images/'+image}/>
            {!cartItems[id]
                ? <img onClick={() => addToCart(id)} src={assets.add_icon_white} className='w-[35px] absolute bottom-[15px] right-[15px] cursor-pointer rounded-[50%]'/> :
                <div className='absolute bottom-[15px] right-[15px] flex items-center gap-[10px] p-[6px] rounded-[50px] bg-white'>
                    <img onClick={() => removeFromCart(id)} src={assets.remove_icon_red}/>
                    <p className='w-[15px]'>{cartItems[id]}</p>
                    <img onClick={() => addToCart(id)} src={assets.add_icon_green}/>
                </div>
            }
        </div>
        <div className='p-[20px]'>
            <div className='flex justify-between items-center mb-[10px]'>
                <p className='text-[20px] font-[500]'>{name}</p>
                <img className='w-[80px]' src={assets.rating_starts}/>
            </div>
            <p className='text-[#676767] text-[15px]'>{description}</p>
            <p className='text-[22px] text-orange-600 font-[500] my-[10px] mx-[0px]'>${price}</p>
        </div>
    </div>
  )
}

export default FoodItem