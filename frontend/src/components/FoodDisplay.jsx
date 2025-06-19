import { useContext, useEffect, useState } from 'react'
import { StoreContext } from '../context/StoreContext';
import FoodItem from './FoodItem';
import '../index.css';

const FoodDisplay = ({category}) => {
    const {food_list, search, showSearch} = useContext(StoreContext);
    const [filterProducts, setFilterProducts] = useState([]);
    const applyFilter = () => {
        let productsCopy = food_list;
        if(showSearch && search) {
            productsCopy = productsCopy.filter(item => item.name.toLowerCase().includes(search.toLowerCase()))
        }
        setFilterProducts(productsCopy);
    }
    useEffect(() => {
        applyFilter();
    }, [search, showSearch, category]);
  return (
    <div className='mt-[20px] flex flex-col gap-[20px]'>
        <h1 className='font-bold text-xl md:text-2xl lg:text-3xl'>Top dishes near you</h1>
        <div className='grid-items-fill mt-[20px] gap-[30px]'>
            {filterProducts.map((item, index) => {
                if(category === 'All' || category === item.category) {
                    return <FoodItem key={index} id={item._id} name={item.name} description={item.description} price={item.price} image={item.image}/>
                }
            })}
        </div>
    </div>
  )
}

export default FoodDisplay